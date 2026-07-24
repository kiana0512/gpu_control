import { mount } from "@vue/test-utils";
import JobsTable from "../src/components/JobsTable.vue";
import StatusMark from "../src/components/StatusMark.vue";
import type { JobInfo } from "../src/types";

describe("StatusMark", () => {
  it("renders state text and semantic class", () => {
    const wrapper = mount(StatusMark, { props: { value: "RESERVED" } });
    expect(wrapper.text()).toContain("已保留");
    expect(wrapper.classes()).toContain("reserved");
  });
});

describe("JobsTable", () => {
  it("renders a sequence batch as one parent row with aggregate details", () => {
    const batch: JobInfo = {
      kind: "batch",
      job_id: "batch-uuid",
      external_batch_id: "animation-shot-001",
      status: "RUNNING",
      workflow_key: "imageclip-rgba",
      workflow_version: "projects-0.2.2",
      priority: "batch",
      node_id: null,
      prompt_id: null,
      progress: 40,
      attempt: 3,
      created_at: "2026-07-24T00:00:00Z",
      started_at: "2026-07-24T00:00:01Z",
      finished_at: null,
      error: null,
      counts: {
        total: 5,
        pending: 2,
        queued: 0,
        running: 1,
        succeeded: 2,
        failed: 0,
        cancelled: 0,
      },
      node_distribution: {
        "control-4090": 1,
        "worker-3090-a": 2,
      },
    };
    const wrapper = mount(JobsTable, {
      props: { jobs: [batch] },
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });
    expect(wrapper.findAll("tbody tr")).toHaveLength(1);
    expect(wrapper.text()).toContain("animation-shot-001");
    expect(wrapper.text()).toContain("序列帧批次");
    expect(wrapper.text()).toContain("2 / 5 帧");
    expect(wrapper.text()).toContain("worker-3090-a · 2");
  });
});
