from incident_agent.cli import _show_usage


def test_usage_counts_cache_writes_reported_per_ttl(capsys):
    _show_usage([
        # First call: the prefix is written to the 5-minute cache; langchain then zeroes cache_creation.
        {"input_tokens": 9000, "output_tokens": 300,
         "input_token_details": {"cache_read": 0, "cache_creation": 0, "ephemeral_5m_input_tokens": 8000,
                                 "ephemeral_1h_input_tokens": 0}},
        # Later call: read from cache, and no per-TTL breakdown.
        {"input_tokens": 10000, "output_tokens": 200,
         "input_token_details": {"cache_read": 8000, "cache_creation": 1500}},
        {"input_tokens": 0, "output_tokens": 0, "input_token_details": {"cache_read": None}},
    ])
    assert ("3 model calls: 19,000 input tokens (8,000 read from cache, 9,500 written to cache), "
            "500 output tokens") in capsys.readouterr().err
