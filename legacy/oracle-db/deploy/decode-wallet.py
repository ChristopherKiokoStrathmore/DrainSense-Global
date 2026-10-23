#!/usr/bin/env python3
"""Decode NGAO_DB_WALLET_B64 into a wallet zip. Does not open a database connection."""

from __future__ import annotations

import argparse
import base64
import binascii
import io
import os
import sys
import zipfile
from pathlib import Path


class WalletError(Exception):
    pass


def decode_wallet(raw: str) -> bytes:
    compact = "".join(raw.split())
    if not compact:
        raise WalletError(
            "NGAO_DB_WALLET_B64 is empty. No wallet was decoded and no connection to NgaoDB was attempted."
        )
    try:
        data = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WalletError(
            "NGAO_DB_WALLET_B64 is not valid standard base64. "
            "Encode the ADB wallet zip with `base64 -w 0` (macOS: `base64 -i Wallet.zip | tr -d '\\n'`). "
            "No connection to NgaoDB was attempted."
        ) from exc
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise WalletError(
            "NGAO_DB_WALLET_B64 decoded, but the bytes are not a zip file. "
            "Use the client-credentials wallet zip downloaded from the Autonomous Database console. "
            "No connection to NgaoDB was attempted."
        )
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [name.replace("\\", "/") for name in archive.namelist()]
    if not any(name.endswith("tnsnames.ora") for name in names):
        raise WalletError(
            "The wallet zip has no tnsnames.ora. Download a fresh instance wallet from NgaoDB. "
            "No connection to NgaoDB was attempted."
        )
    if not any(name.endswith("cwallet.sso") for name in names):
        raise WalletError(
            "The wallet zip has no cwallet.sso. SQLcl uses that file for the mTLS connection. "
            "No connection to NgaoDB was attempted."
        )
    return data


def self_test() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("tnsnames.ora", "ngaodb_low = (description=(address=(protocol=tcps)))\n")
        archive.writestr("cwallet.sso", b"not-a-real-wallet")
    good = base64.b64encode(buffer.getvalue()).decode()
    decoded = decode_wallet(good)
    if not zipfile.is_zipfile(io.BytesIO(decoded)):
        raise AssertionError("round trip was not a zip")

    wrapped = "\n".join(good[i : i + 16] for i in range(0, len(good), 16))
    decode_wallet(wrapped)

    try:
        decode_wallet("!!!!")
    except WalletError as exc:
        if "base64" not in str(exc):
            raise AssertionError(exc) from exc
    else:
        raise AssertionError("invalid base64 should fail")

    try:
        decode_wallet(base64.b64encode(b"hello").decode())
    except WalletError as exc:
        if "not a zip" not in str(exc):
            raise AssertionError(exc) from exc
    else:
        raise AssertionError("non-zip should fail")

    thin = io.BytesIO()
    with zipfile.ZipFile(thin, "w") as archive:
        archive.writestr("readme.txt", "no wallet")
    try:
        decode_wallet(base64.b64encode(thin.getvalue()).decode())
    except WalletError as exc:
        if "tnsnames.ora" not in str(exc):
            raise AssertionError(exc) from exc
    else:
        raise AssertionError("zip without tnsnames.ora should fail")

    print("decode_wallet self-test ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decode the ADB wallet secret")
    parser.add_argument("--out", type=Path, help="Where to write the wallet zip")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if args.out is None:
        print("::error::decode-wallet.py requires --out", file=sys.stderr)
        return 1
    try:
        data = decode_wallet(os.environ.get("NGAO_DB_WALLET_B64", ""))
    except WalletError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(data)
    args.out.chmod(0o600)
    print(f"Wallet zip written ({len(data)} bytes). Contents were not printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
