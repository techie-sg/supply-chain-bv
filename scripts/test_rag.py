from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

sys.path.insert(0, str(SRC_DIR))

from dispatchdesk.config import REPO_ROOT

from dispatchdesk.rag import answer_question


def main() -> None:
    question = "Why are my deliveries slipping when it rains?"

    print("=== DispatchDesk RAG Test ===")
    print(f"\nQuestion: {question}\n")

    answer = answer_question(question)

    print("Answer:")
    print(answer)


if __name__ == "__main__":
    main()
