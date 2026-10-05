#!/usr/bin/env bash
# Runs inside the eval scaffold dir (only with --scaffold).
cat > tickets.csv <<'C'
id,state
a1,Checkout returns HTTP 500 for every customer since the deploy.
a2,Could you change the footer link colour? Purely cosmetic.
a3,Customer export is leaking other tenants' rows.
a4,How do I rename a project in settings?
C
