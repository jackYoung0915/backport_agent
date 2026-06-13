from __future__ import annotations

import re
from collections import defaultdict

from .models import DependencyEdge, DependencyGraph, SpecInfo


def build_dependency_graph(specs: list[SpecInfo]) -> DependencyGraph:
    repos = tuple(sorted({spec.repo for spec in specs}))
    package_index: dict[str, str] = {}

    for spec in sorted(specs, key=lambda item: item.repo):
        for package_name in sorted(set(spec.packages) | set(spec.provides) | {spec.source_name}):
            package_index.setdefault(package_name, spec.repo)

    edge_keys: set[tuple[str, str, str]] = set()
    external: dict[str, set[str]] = defaultdict(set)

    for spec in specs:
        for requirement in spec.requires:
            target_repo = package_index.get(requirement)
            if target_repo and target_repo != spec.repo:
                edge_keys.add((spec.repo, target_repo, requirement))
            elif not target_repo:
                external[spec.repo].add(requirement)

    edges = tuple(DependencyEdge(*edge) for edge in sorted(edge_keys))
    external_requires = {repo: tuple(sorted(values)) for repo, values in sorted(external.items())}
    return DependencyGraph(repos=repos, edges=edges, external_requires=external_requires)


def render_mermaid(graph: DependencyGraph) -> str:
    lines = ["flowchart LR"]
    for repo in graph.repos:
        lines.append(f'    {_node_id(repo)}["{repo}"]')
    for edge in graph.edges:
        lines.append(
            f"    {_node_id(edge.source_repo)} -->|{edge.dependency}| {_node_id(edge.target_repo)}"
        )
    return "\n".join(lines) + "\n"


def render_dot(graph: DependencyGraph) -> str:
    lines = ["digraph install_dependencies {", "    rankdir=LR;"]
    for repo in graph.repos:
        lines.append(f'    "{repo}";')
    for edge in graph.edges:
        lines.append(f'    "{edge.source_repo}" -> "{edge.target_repo}" [label="{edge.dependency}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def graph_to_dict(graph: DependencyGraph) -> dict[str, object]:
    return {
        "repos": list(graph.repos),
        "edges": [
            {
                "source_repo": edge.source_repo,
                "target_repo": edge.target_repo,
                "dependency": edge.dependency,
            }
            for edge in graph.edges
        ],
        "external_requires": {repo: list(values) for repo, values in graph.external_requires.items()},
    }


def _node_id(repo: str) -> str:
    node = re.sub(r"[^A-Za-z0-9_]", "_", repo)
    if node and node[0].isdigit():
        return f"repo_{node}"
    return node
