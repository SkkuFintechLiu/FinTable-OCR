#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

bash packaging/build_macos.sh
open dist
open dist/DebtExtractor.app
