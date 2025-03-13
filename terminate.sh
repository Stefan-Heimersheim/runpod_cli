#!/bin/bash
# Signal RunPod to terminate this pod
echo "Requesting pod termination..."
curl --request POST \
  --header 'content-type: application/json' \
  --url "https://api.runpod.io/graphql?api_key=${RUNPOD_API_KEY}" \
  --data "{\"query\": \"mutation { podTerminate(input: {podId: \\\"${RUNPOD_POD_ID}\\\"}) }\"}"

echo "Termination request sent. Pod should stop shortly."
exit 0