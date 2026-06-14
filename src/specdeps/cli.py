from __future__ import annotations

import argparse
import json
from pathlib import Path

from .fetcher import checkout_repos, find_spec_files
from .graph import build_dependency_graph, graph_to_dict, render_dot, render_mermaid
from .models import SpecInfo
from .repo_config import load_repos
from .report import render_report
from .spec_parser import parse_spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract install dependency topology from RPM spec repositories")
    parser.add_argument("--config", default="config/repos.json", help="Path to repository config JSON")
    parser.add_argument("--checkout-dir", default="work/repos", help="Directory where repositories are cloned")
    parser.add_argument("--out-dir", default="out", help="Directory where graph outputs are written")
    parser.add_argument("--skip-fetch", action="store_true", help="Use existing checkout directories")
    args = parser.parse_args(argv)

    repos = load_repos(args.config)
    checkout_root = Path(args.checkout_dir)
    repo_paths = (
        {repo.name: checkout_root / repo.name for repo in repos}
        if args.skip_fetch
        else checkout_repos(repos, checkout_root)
    )

    specs: list[SpecInfo] = []
    for repo in repos:
        spec_files = find_spec_files(repo_paths[repo.name])
        if not spec_files:
            raise FileNotFoundError(f"no spec files found for {repo.name} in {repo_paths[repo.name]}")
        for spec_path in spec_files:
            specs.append(parse_spec(spec_path, repo.name))

    graph = build_dependency_graph(specs)
    mermaid = render_mermaid(graph)
    dot = render_dot(graph)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dependency_payload = graph_to_dict(graph)
    dependency_payload["specs"] = [
        {
            "repo": spec.repo,
            "spec_path": spec.spec_path,
            "source_name": spec.source_name,
            "packages": sorted(spec.packages),
            "provides": sorted(spec.provides),
            "requires": list(spec.requires),
        }
        for spec in sorted(specs, key=lambda item: (item.repo, item.spec_path))
    ]

    (out_dir / "dependencies.json").write_text(
        json.dumps(dependency_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "dependency-topology.mmd").write_text(mermaid, encoding="utf-8")
    (out_dir / "dependency-topology.dot").write_text(dot, encoding="utf-8")
    (out_dir / "dependency-report.md").write_text(render_report(graph, specs, mermaid), encoding="utf-8")

    print(f"Wrote {out_dir / 'dependencies.json'}")
    print(f"Wrote {out_dir / 'dependency-topology.mmd'}")
    print(f"Wrote {out_dir / 'dependency-topology.dot'}")
    print(f"Wrote {out_dir / 'dependency-report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
