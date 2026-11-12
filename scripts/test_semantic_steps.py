from src.agents.semantic_clean import run_semantic_swap_agent, run_semantic_taxonomy_agent

if __name__ == "__main__":
    print("=" * 80)
    print("TESTING SEMANTIC CLASSIFICATION AGENT (STEPS 1 & 2)")
    print("=" * 80)

    # 1. Run Step 1: Field Swap Resolution
    swap_results = run_semantic_swap_agent()
    print(f"\n[Step 1 Complete] Inspected {len(swap_results)} candidate records.")
    
    print("\nSample Swapped Records Resolved:")
    for r in swap_results[:5]:
        print(f"\nTicket ID: {r.ticket_id} (Swapped: {r.is_swapped})")
        print(f"  Reasoning        : {r.semantic_reasoning}")
        print(f"  Clean Category   : {r.clean_category}")
        print(f"  Clean Description: {r.clean_description[:70]}...")

    # 2. Run Step 2: Canonical Taxonomy Mapping
    taxonomy_results = run_semantic_taxonomy_agent()
    print(f"\n[Step 2 Complete] Mapped {len(taxonomy_results)} unique categories.")

    print("\nSample Taxonomy Classifications (First 15):")
    for item in taxonomy_results[:15]:
        print(f"  * '{item.raw_category}' -> [{item.canonical_category}] (Conf: {item.confidence}) | Rationale: {item.rationale}")

    # Inspect how slang/noise was handled
    print("\nVerification of Semantic Slang & Noise Handling:")
    test_terms = ["cold", "pipe burst", "WiFi", "DELETE ME", "asdf", "test", "Lift", "Sprinkler"]
    term_dict = {m.raw_category: m.canonical_category for m in taxonomy_results}
    for term in test_terms:
        print(f"  * '{term}' => {term_dict.get(term, 'NOT_FOUND')}")