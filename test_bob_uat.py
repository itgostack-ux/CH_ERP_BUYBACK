#!/usr/bin/env python
"""
Bank of Baroda UAT Integration Test - Frappe Bench Runner
=========================================================

Run with: bench --site erpnext.local exec /home/palla/erpnext-bench/scripts/test_bob_uat.py

This script tests BoB's new Olive platform endpoints for payment transactions
and inquiries using the provided UAT credentials.
"""

import json
import sys
import os
from pathlib import Path

# Add the buyback app to the Python path
buyback_path = Path(__file__).parent.parent / "apps" / "buyback"
sys.path.insert(0, str(buyback_path))

from buyback.bob_uat_test import run_bob_uat_tests

def main():
    """Main entry point"""
    try:
        run_bob_uat_tests()
        return 0
    except KeyboardInterrupt:
        print("\n\n🛑 Tests interrupted by user")
        return 130
    except Exception as e:
        print(f"\n\n❌ Test failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
