#!/bin/bash
export OPENSEARCH_URL="http://localhost:9201"
cd ~/india-gov-mcp-poc
npx @modelcontextprotocol/inspector uvx opensearch-mcp-server-py
export DANGEROUSLY_OMIT_AUTH=true
