from dpdfnet_mic_bridge.cli.parser import build_parser


def test_run_realtime_option_parses():
    args = build_parser().parse_args(["run", "--realtime", "strict"])
    assert args.command == "run"
    assert args.realtime == "strict"


def test_realtime_status_parses():
    args = build_parser().parse_args(["realtime", "status", "--json"])
    assert args.command == "realtime"
    assert args.realtime_command == "status"
    assert args.json is True


def test_realtime_apply_parses():
    args = build_parser().parse_args(
        ["realtime", "apply", "--yes", "--rtprio", "40", "--nice", "-5"]
    )
    assert args.command == "realtime"
    assert args.realtime_command == "apply"
    assert args.yes is True
    assert args.rtprio == 40
    assert args.nice == -5
