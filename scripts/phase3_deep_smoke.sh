#!/usr/bin/env bash
# Manual, opt-in smoke for the Phase 3 deep worker — exercises the REAL `claude -p`
# running a full /marshal review. NOT part of CI (real LLM cost + minutes).
#
# Usage:  REPO=node CHANGE_REF=<sha> bash scripts/phase3_deep_smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${REPO:-node}"
CHANGE_REF="${CHANGE_REF:?set CHANGE_REF to a commit SHA present in /home/ubuntu/workspace/$REPO}"
export MARSHAL_DB="sqlite:///$PWD/phase3_smoke.db"
export MARSHAL_DEEP_TIMEOUT_S="${MARSHAL_DEEP_TIMEOUT_S:-1800}"
PY=venv/bin/python

echo "Enqueuing a deep job for $REPO@$CHANGE_REF ..."
$PY -c "
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from marshal_core.knowledge.models import Base
from marshal_core.knowledge.store import Store
from marshal_pack_cowboy.pack import CowboyPack
from marshal_core.worker import run_once
e=create_engine(os.environ['MARSHAL_DB']); Base.metadata.create_all(e); S=sessionmaker(bind=e)
with S() as s:
    st=Store(s); j=st.enqueue_job(change_ref='$CHANGE_REF', repo='$REPO', kind='deep')
    print('enqueued job', j['id'], '- running worker tick (this invokes real claude)...')
    run_once(st, CowboyPack())
    print('final:', st.get_job(j['id']))
"
rm -f phase3_smoke.db
echo "Done. A 'done' status with result.verdict + gate_run_id means the deep path works end to end."
