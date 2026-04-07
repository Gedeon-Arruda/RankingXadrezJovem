import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wrapper de compatibilidade para o gerador Node do Ranking Xadrez Jovem."
    )
    parser.add_argument(
        "--source",
        choices=("all", "lichess", "chesscom"),
        default="all",
        help="Fonte a gerar."
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Imprime o payload em stdout em vez de gravar arquivos."
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent
    node_binary = shutil.which("node")

    if not node_binary:
        print("Erro: Node.js nao encontrado no PATH.", file=sys.stderr)
        return 1

    command = [
        node_binary,
        str(project_root / "tools" / "generate-data.mjs"),
        "--source",
        args.source
    ]

    if args.stdout:
        command.append("--stdout")

    result = subprocess.run(command, cwd=project_root)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
