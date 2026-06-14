from __future__ import annotations

from collections import defaultdict, deque

from ..models import TopologyData


def dependency_closure(topology: TopologyData, selected_repos: tuple[str, ...] | None) -> frozenset[str]:
    if not selected_repos:
        return frozenset(topology.repos)

    known = set(topology.repos)
    requested = set(selected_repos)
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(f"unknown repositories: {', '.join(unknown)}")

    providers_by_consumer: dict[str, set[str]] = defaultdict(set)
    for source, target, _dependency in topology.edges:
        if source != target:
            providers_by_consumer[source].add(target)

    closure = set(requested)
    queue = deque(sorted(requested))
    while queue:
        repo = queue.popleft()
        for provider in sorted(providers_by_consumer.get(repo, set())):
            if provider not in closure:
                closure.add(provider)
                queue.append(provider)
    return frozenset(closure)


def install_order(topology: TopologyData, selected_repos: tuple[str, ...] | None) -> tuple[str, ...]:
    repos = dependency_closure(topology, selected_repos)
    return _topological_order(topology, repos)


def uninstall_order(topology: TopologyData, selected_repos: tuple[str, ...] | None) -> tuple[str, ...]:
    repos = dependency_closure(topology, selected_repos)
    return tuple(reversed(_topological_order(topology, repos)))


def external_dependents(topology: TopologyData, selected_repos: tuple[str, ...] | None) -> dict[str, tuple[str, ...]]:
    repos = dependency_closure(topology, selected_repos)
    dependents_by_provider: dict[str, set[str]] = defaultdict(set)
    for source, target, _dependency in topology.edges:
        if source == target:
            continue
        if target in repos and source not in repos:
            dependents_by_provider[target].add(source)
    return {
        repo: tuple(sorted(dependents))
        for repo, dependents in sorted(dependents_by_provider.items())
    }


def _topological_order(topology: TopologyData, repos: frozenset[str]) -> tuple[str, ...]:
    outgoing: dict[str, set[str]] = {repo: set() for repo in repos}
    indegree: dict[str, int] = {repo: 0 for repo in repos}

    for source, target, _dependency in topology.edges:
        if source not in repos or target not in repos or source == target:
            continue
        before, after = target, source
        if after not in outgoing[before]:
            outgoing[before].add(after)
            indegree[after] += 1

    ready = deque(sorted(repo for repo, count in indegree.items() if count == 0))
    ordered: list[str] = []
    while ready:
        repo = ready.popleft()
        ordered.append(repo)
        for dependent in sorted(outgoing[repo]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)

    if len(ordered) != len(repos):
        raise ValueError("dependency cycle detected in topology")
    return tuple(ordered)
