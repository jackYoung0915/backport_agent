from __future__ import annotations

from .models import DependencyGraph, SpecInfo


def render_report(graph: DependencyGraph, specs: list[SpecInfo], mermaid: str) -> str:
    lines: list[str] = [
        "# RPM Spec Install Dependency Topology",
        "",
        "## Summary",
        "",
        f"- Repositories: {len(graph.repos)}",
        f"- Internal dependency edges: {len(graph.edges)}",
        f"- Repositories with external install requirements: {len(graph.external_requires)}",
        "",
        "## Topology",
        "",
        "```mermaid",
        mermaid.rstrip(),
        "```",
        "",
        "## Internal Edges",
        "",
        "| Source Repository | Target Repository | Required Package |",
        "| --- | --- | --- |",
    ]

    if graph.edges:
        for edge in graph.edges:
            lines.append(f"| {edge.source_repo} | {edge.target_repo} | {edge.dependency} |")
    else:
        lines.append("| No internal dependency edges found |  |  |")

    lines.extend(["", "## Parsed Specs", "", "| Repository | Spec Path | Packages | Provides |", "| --- | --- | --- | --- |"])
    for spec in sorted(specs, key=lambda item: item.repo):
        packages = ", ".join(sorted(spec.packages))
        provides = ", ".join(sorted(spec.provides)) if spec.provides else "-"
        lines.append(f"| {spec.repo} | {spec.spec_path} | {packages} | {provides} |")

    lines.extend(["", "## External Install Requirements", ""])
    if graph.external_requires:
        for repo, requirements in graph.external_requires.items():
            formatted = ", ".join(f"`{requirement}`" for requirement in requirements)
            lines.append(f"- `{repo}`: {formatted}")
    else:
        lines.append("- None")

    return "\n".join(lines) + "\n"
