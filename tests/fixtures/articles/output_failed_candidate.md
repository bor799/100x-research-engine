# Output Failure Candidate

This fixture should fetch, score, and extract successfully. The staging output port
then simulates an output failure so the queue cannot be marked done without a closed
output loop.
