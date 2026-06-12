from unshuffle.core import LibNode
from unshuffle.core import NodeType
from unshuffle.logic.classification import classify_node, get_scoring_engine
from pathlib import Path

def test():
    get_scoring_engine()
    
    cases = [
        ("808 kick.wav", "Kicks"),
        ("kick kick kick.wav", "Kicks"),
        ("clap.wav", "Claps"),
        ("808.wav", "Bass"),
    ]
    
    print(f"{'Filename':<30} | {'Category':<15} | {'Confidence':<10}")
    print("-" * 60)
    for name, expected in cases:
        node = LibNode(name=name, path=Path(f"D:/Test/{name}"), node_type=NodeType.FILE)
        cat, conf, meta = classify_node(node)
        print(f"{name:<30} | {cat:<15} | {conf:.2%}")

if __name__ == "__main__":
    test()
