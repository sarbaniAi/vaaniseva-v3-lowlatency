#!/bin/bash
# Add all known Twilio Serverless (Functions) IP ranges to Databricks IP ACL.
# These are the egress IPs used by Twilio Functions when making outbound HTTPS calls.
# Source: https://www.twilio.com/docs/serverless/functions-assets/ip-addresses
#
# Usage: ./scripts/add_twilio_ips.sh [PROFILE]

PROFILE="${1:-DEFAULT}"

echo "Adding Twilio Functions egress IPs to workspace IP access list..."

# Twilio Functions egress IPs (US1 region — default for most accounts)
# Last updated: April 2026
# These cover Twilio Serverless, not SIP trunking
databricks ip-access-lists create --json '{
  "label": "twilio-functions-us1",
  "list_type": "ALLOW",
  "ip_addresses": [
    "34.203.95.0/24",
    "34.226.36.0/24",
    "34.234.0.0/16",
    "44.220.0.0/16",
    "52.0.0.0/11",
    "54.80.0.0/12",
    "54.160.0.0/12",
    "100.24.0.0/13",
    "3.208.0.0/12"
  ]
}' -p "$PROFILE" -o json 2>&1

if [ $? -eq 0 ]; then
    echo "Done! Twilio Functions IPs added."
else
    echo "Failed. You may need to add IPs manually in the Databricks workspace UI."
    echo ""
    echo "Alternative: Add a broad allow rule for Twilio's AWS us-east-1 ranges:"
    echo "  34.234.0.0/16, 44.220.0.0/16, 52.0.0.0/11, 54.80.0.0/12"
    echo ""
    echo "Or add specific IPs as they appear in error logs."
fi
