#!/bin/bash
# Proof #2/#3 (NHAUAPP side): put via a QALIAS -> local audit retained + duplicate
# streamed to a QREMOTE that reuses the EXISTING XMITQ(SVCQM) + channel NHAUAPP.SVCQM.
# Adds only GW.EXP.* scratch objects. Prints the retained audit copy's MessageId.
set -u
QM=NHAUAPP
mq(){ su mqm -c "/opt/mqm/bin/runmqsc $QM"; }
echo "### define outbound scratch objects on NHAUAPP (reuse XMITQ(SVCQM)+chl NHAUAPP.SVCQM) ###"
cat <<'EOF' | mq | grep -iE "AMQ8006|AMQ8008|error|not auth"
DEFINE QLOCAL('GW.EXP.PRIMARY') STREAMQ('GW.EXP.REMOTE') STRMQOS(MUSTDUP) REPLACE
DEFINE QREMOTE('GW.EXP.REMOTE') RNAME('GW.EXP.REMOTE.TARGET') RQMNAME('SVCQM') XMITQ('SVCQM') REPLACE
DEFINE QALIAS('GW.EXP.ALIAS') TARGET('GW.EXP.PRIMARY') REPLACE
EOF
echo "### sender channel NHAUAPP.SVCQM status (expect RUNNING) ###"
printf "DISPLAY CHSTATUS('NHAUAPP.SVCQM') STATUS\n" | mq | grep -iE "CHSTATUS\(|STATUS\("
echo "### PUT one message via the ALIAS ###"
echo "remote-msgid-proof" | su mqm -c "/opt/mqm/samp/bin/amqsput GW.EXP.ALIAS $QM" 2>&1 | grep -iE "start|end|reason" | head -2
sleep 2
echo "### retained AUDIT copy on NHAUAPP (GW.EXP.PRIMARY) — MessageId + payload ###"
su mqm -c "/opt/mqm/samp/bin/amqsbcg GW.EXP.PRIMARY $QM" 2>/dev/null | grep -iE "MessageId|remote-msgid" | head -3
printf "DISPLAY QL('GW.EXP.PRIMARY') CURDEPTH\nDISPLAY QL('SVCQM') CURDEPTH\n" | mq | grep -iE "CURDEPTH"
