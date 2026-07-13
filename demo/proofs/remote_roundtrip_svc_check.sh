#!/bin/bash
# Proof #2/#3 (SVCQM side): the message streamed from NHAUAPP should have arrived
# on GW.EXP.REMOTE.TARGET across the real channel, with the SAME MessageId.
set -u
QM=SVCQM
echo "### delivered copy on SVCQM (GW.EXP.REMOTE.TARGET) — MessageId + payload ###"
su mqm -c "/opt/mqm/samp/bin/amqsbcg GW.EXP.REMOTE.TARGET $QM" 2>/dev/null | grep -iE "MessageId|remote-msgid" | head -3
printf "DISPLAY QL('GW.EXP.REMOTE.TARGET') CURDEPTH\n" | su mqm -c "/opt/mqm/bin/runmqsc $QM" | grep -iE "CURDEPTH"
