from gui.core.search_engine import SearchEngine

def test_prefixes():
    test_cases = [
        ("cat:drums", "category:drums"),
        ("pack:808", "pack:808"),
        ("file:kick", "sample_name:kick"),
        ("tag:heavy", "tags:heavy"),
        ("type:oneshot", "audio_type:oneshot"),
        ("conf:>80", "confidence:>80"),
    ]
    
    for input_term, expected in test_cases:
        actual = SearchEngine.canonicalize_term(input_term)
        print(f"Input: {input_term} -> Actual: {actual} (Expected: {expected})")
        assert actual == expected

if __name__ == "__main__":
    try:
        test_prefixes()
        print("Prefix test PASSED")
    except Exception as e:
        print(f"Prefix test FAILED: {e}")
        exit(1)
