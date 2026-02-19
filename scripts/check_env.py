#!/usr/bin/env python3
"""Validate configuration: config loading, GHES connection, LLM connection."""

import sys


def check_config():
    """1단계: .env 로드 및 필수값 검증."""
    print("[1/3] Loading config from .env ...")
    try:
        from workrecap.config import AppConfig

        config = AppConfig()
        print(f"  GHES_URL      = {config.ghes_url}")
        print(f"  USERNAME       = {config.username}")
        print(f"  DATA_DIR      = {config.data_dir}")

        config_path = config.provider_config_path
        if config_path.exists():
            print(f"  config.toml   = {config_path} (found)")
        else:
            print(f"  config.toml   = {config_path} (NOT FOUND)")
            print("  => FAIL: .provider/config.toml is required")
            return None

        print("  => OK")
        return config
    except Exception as e:
        print(f"  => FAIL: {e}")
        return None


def check_ghes(config):
    """2단계: GHES API 연결 확인."""
    print("\n[2/3] Testing GHES connection ...")
    try:
        from workrecap.infra.ghes_client import GHESClient

        with GHESClient(config.ghes_url, config.ghes_token) as client:
            resp = client._client.get("/user")
            resp.raise_for_status()
            login = resp.json().get("login", "?")
            print(f"  Authenticated as: {login}")
            print("  => OK")
            return True
    except Exception as e:
        print(f"  => FAIL: {e}")
        return False


def check_llm(config):
    """3단계: LLM API 연결 확인."""
    print("\n[3/3] Testing LLM connection ...")
    try:
        from workrecap.infra.llm_router import LLMRouter
        from workrecap.infra.provider_config import ProviderConfig
        from workrecap.infra.usage_tracker import UsageTracker
        from workrecap.infra.pricing import PricingTable

        pc = ProviderConfig(config.provider_config_path)
        tracker = UsageTracker(pricing=PricingTable())
        llm = LLMRouter(pc, usage_tracker=tracker)
        reply = llm.chat("You are a test assistant.", "Reply with just: OK")
        print(f"  Response: {reply.strip()}")
        print("  => OK")
        return True
    except Exception as e:
        print(f"  => FAIL: {e}")
        return False


def main():
    config = check_config()
    if config is None:
        print("\nResult: config loading failed. Check your .env and .provider/config.toml.")
        sys.exit(1)

    ghes_ok = check_ghes(config)
    llm_ok = check_llm(config)

    print("\n" + "=" * 40)
    print(f"  Config : OK")
    print(f"  GHES   : {'OK' if ghes_ok else 'FAIL'}")
    print(f"  LLM    : {'OK' if llm_ok else 'FAIL'}")
    print("=" * 40)

    if ghes_ok and llm_ok:
        print("All checks passed!")
    else:
        print("Some checks failed. Review the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
