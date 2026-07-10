def test_openevolve_imports_run_judge_and_run_mutator_from_evolve():
    from vibe_serve.loops.evolve import loop as ev_loop
    from vibe_serve.loops.openevolve import loop as oe_loop

    assert oe_loop._run_judge is ev_loop._run_judge
    assert oe_loop._run_mutator is ev_loop._run_mutator
