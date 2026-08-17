import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from peakflow import config


def test_client_types_expected():
    assert len(config.CLIENT_TYPES) == 8
    assert config.CLIENT_TYPES[0] == "M1"
    assert config.CLIENT_TYPES[7] == "repay_3out"


def test_horizon():
    assert config.HORIZON == 30
    assert config.BACKTEST_WINDOW <= config.MIN_HISTORY


def test_paths():
    assert config.DATA_DIR.name == "data"
    assert config.OUTPUT_DIR.name == "output"


def main():
    test_client_types_expected()
    test_horizon()
    test_paths()
    print("test_peakflow_config OK")


if __name__ == "__main__":
    main()
