from src.twin import classify_run, decide, other_repo


def test_repos_point_at_each_other():
    assert other_repo("AbhilashMunnur/nifty-index-trade") == "AbhilashMunnur/nifty-index-scan"
    assert other_repo("AbhilashMunnur/nifty-index-scan") == "AbhilashMunnur/nifty-index-trade"


def test_a_refused_job_is_the_quota_signal():
    run = {"status": "completed", "conclusion": "failure"}
    assert classify_run(run, [{"steps": []}]) == "blocked"
    assert classify_run(run, []) == "blocked"
    assert classify_run({"status": "completed", "conclusion": "startup_failure"}, []) == "blocked"


def test_a_started_failure_belongs_to_repo_1():
    run = {"status": "completed", "conclusion": "failure"}
    assert classify_run(run, [{"steps": [{"name": "Run scan"}]}]) == "done"


def test_standby_scans_only_when_repo_1_cannot():
    assert decide(["done"], appear_deadline_passed=True) == "yield"
    assert decide(["pending"], appear_deadline_passed=True) == "wait"
    assert decide(["blocked"], appear_deadline_passed=False) == "scan"
    assert decide(["blocked", "pending"], appear_deadline_passed=True) == "wait"
    assert decide([], appear_deadline_passed=False) == "wait"
    assert decide([], appear_deadline_passed=True) == "scan"
    assert decide(["done", "blocked"], appear_deadline_passed=True) == "yield"
