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


async def test_end_of_input_at_the_approval_prompt_rejects_the_rest(monkeypatch, capsys):
    from incident_agent import cli
    from incident_agent.remediation import idempotency_key
    from incident_agent.report import InvestigationReport, RemediationAction

    actions = [RemediationAction(kind="restart_deployment", deployment=d, replicas=None, reason="RB-001.")
               for d in ("delta-ticker", "orders-api")]
    report = InvestigationReport(root_cause="Half-open socket.", confidence="high", next_step="Restart.",
                                 evidence=[{"finding": "f", "source": "prometheus", "reference": "md_ws_connected"}],
                                 remediation=actions)
    proposals = [{"key": idempotency_key("run", a), "action": a.model_dump()} for a in actions]
    answers = iter(["y"])

    def fake_input(prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", fake_input)
    decision = await cli._ask_approval(report, proposals, dry_run=False)

    assert decision.approved == [proposals[0]["key"]]
    assert "End of input: rejecting the remaining actions." in capsys.readouterr().err
