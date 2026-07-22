import json
import os
import uuid

from locust import HttpUser, between, task


class JobSubmitter(HttpUser):
    wait_time = between(0.05, 0.2)

    @task
    def submit(self) -> None:
        api_key = os.environ.get("LOAD_TEST_API_KEY", "")
        if not api_key:
            raise RuntimeError("LOAD_TEST_API_KEY is required")
        files = {
            "workflow_key": (None, os.environ.get("LOAD_TEST_WORKFLOW", "fake")),
            "workflow_version": (None, os.environ.get("LOAD_TEST_WORKFLOW_VERSION", "1")),
            "parameters": (None, json.dumps({"steps": 20})),
        }
        with self.client.post(
            "/api/v1/jobs",
            files=files,
            headers={"X-API-Key": api_key, "Idempotency-Key": f"load-{uuid.uuid4()}"},
            catch_response=True,
        ) as response:
            if response.status_code not in {200, 202, 429}:
                response.failure(f"unexpected status {response.status_code}: {response.text[:200]}")
