#!/bin/bash
# Proof #1 (+#4) for the gateway remote audit design. Runs on the SVCQM node.
# Q: Does CAPEXPRY on a STREAMING PRIMARY cap the RETAINED ORIGINAL, while the
#    STREAMED copy stays unlimited? And does streaming fire via a QALIAS put?
# Adds only GW.EXP.* scratch objects; cleans them up at the end. Read/verify only
# against our own objects — the lab's queues are never touched (parasite rule).
set -u
QM=SVCQM
MQSC=/opt/mqm/bin/runmqsc
PUT=/opt/mqm/samp/bin/amqsput
BCG=/opt/mqm/samp/bin/amqsbcg
mqsc() { su mqm -c "$MQSC $QM"; }

echo "### DEFINE scratch objects (CAPEXPRY 100 = 10s on the primary) ###"
cat <<'EOF' | mqsc | grep -iE "AMQ8006|AMQ8008|AMQ8148|AMQ9|error|not"
DEFINE QLOCAL('GW.EXP.STREAM') REPLACE
DEFINE QLOCAL('GW.EXP.PRIMARY') CAPEXPRY(100) STREAMQ('GW.EXP.STREAM') STRMQOS(MUSTDUP) REPLACE
DEFINE QALIAS('GW.EXP.ALIAS') TARGET('GW.EXP.PRIMARY') REPLACE
EOF

echo "### PUT one message via the ALIAS (proof #4: alias -> streaming) ###"
echo "hello-capexpry-proof" | su mqm -c "$PUT GW.EXP.ALIAS $QM" 2>&1 | grep -iE "start|end|complet|reason|target" | head -3

echo "### T0 depths (expect PRIMARY curdepth 1, STREAM curdepth 1 = streaming fired via alias) ###"
printf "DISPLAY QL('GW.EXP.PRIMARY') CURDEPTH\nDISPLAY QL('GW.EXP.STREAM') CURDEPTH\n" | mqsc | grep -iE "CURDEPTH"

echo "### T0 PRIMARY retained original — Expiry should be CAPPED (~100, ticking down) ###"
su mqm -c "$BCG GW.EXP.PRIMARY $QM" 2>/dev/null | grep -iE "Expiry|hello-cap" | head -3
echo "### T0 STREAM streamed copy — Expiry should be UNLIMITED (-1) ###"
su mqm -c "$BCG GW.EXP.STREAM $QM" 2>/dev/null | grep -iE "Expiry|hello-cap" | head -3

echo "### waiting 20s (> CAPEXPRY 10s) ###"
sleep 20

echo "### T1 PRIMARY — retained original should now be EXPIRED/gone (no hello) ###"
su mqm -c "$BCG GW.EXP.PRIMARY $QM" 2>/dev/null | grep -iE "hello-cap|No more" | head -3
printf "DISPLAY QL('GW.EXP.PRIMARY') CURDEPTH\n" | mqsc | grep -iE "CURDEPTH"
echo "### T1 STREAM — streamed copy should STILL be present (unlimited) ###"
su mqm -c "$BCG GW.EXP.STREAM $QM" 2>/dev/null | grep -iE "hello-cap|No more" | head -3
printf "DISPLAY QL('GW.EXP.STREAM') CURDEPTH\n" | mqsc | grep -iE "CURDEPTH"

echo "### CLEANUP (delete GW.EXP.*) ###"
printf "DELETE QALIAS('GW.EXP.ALIAS')\nDELETE QL('GW.EXP.PRIMARY') PURGE\nDELETE QL('GW.EXP.STREAM') PURGE\n" | mqsc | grep -iE "AMQ8016|AMQ8064|error" | head
echo "### DONE ###"
