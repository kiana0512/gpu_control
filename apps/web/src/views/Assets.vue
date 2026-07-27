<script setup lang="ts">
const workers = [
  {
    name: "3090-A CPU Worker",
    host: "lilithgames1",
    address: "10.3.34.12",
    slots: 3,
    state: "镜像已验证 · 待控制面启用",
  },
  {
    name: "3090-B CPU Worker",
    host: "lilithgames3",
    address: "10.3.34.4",
    slots: 8,
    state: "镜像已验证 · 待控制面启用",
  },
  {
    name: "4090 CPU Worker",
    host: "control-4090",
    address: "10.3.34.11",
    slots: 2,
    state: "候选节点 · 尚未部署",
  },
];

const artifacts = [
  "model_PBR_UV.blend",
  "model_PBR_UV.fbx",
  "model_report.json",
  "model_QA.json",
];
</script>

<template>
  <div class="page assets-page">
    <div class="page-heading asset-heading">
      <div>
        <div class="eyebrow">CPU 资产处理平面</div>
        <h1>Blender PBR 自动拆 UV</h1>
        <p>独立于 GPU 推理队列的高并发资产处理能力</p>
      </div>
      <span class="candidate-state"><i></i>候选功能 · 后端安全窗口待启用</span>
    </div>

    <section class="asset-notice">
      <div>
        <strong>生产 GPU 后端保持冻结</strong>
        <p>
          当前页面展示已经完成的部署候选，不会领取生产任务。待现有任务清空后，单独启用 Asset API、数据库增量迁移和 Worker 心跳。
        </p>
      </div>
      <span>0 个资产任务接入</span>
    </section>

    <div class="asset-summary">
      <section>
        <span>候选 CPU 并发</span><strong>13</strong><small>3 台主机独立槽位</small>
      </section>
      <section>
        <span>任务隔离</span><strong>独立</strong><small>不占用 GPU Job 槽位</small>
      </section>
      <section>
        <span>交付规则</span><strong>原子</strong><small>四件套全部通过才发布</small>
      </section>
      <section>
        <span>Blender</span><strong>5.1.2</strong><small>固定构建 ec6e62d40fa9</small>
      </section>
    </div>

    <div class="asset-layout">
      <section class="asset-card worker-card">
        <header>
          <div><h2>CPU Worker</h2><p>按主机 CPU、内存、负载和租约动态限流</p></div>
          <span>部署候选</span>
        </header>
        <div v-for="worker in workers" :key="worker.name" class="asset-worker-row">
          <div class="worker-identity"><i></i><div><strong>{{ worker.name }}</strong><small>{{ worker.host }} · {{ worker.address }}</small></div></div>
          <div><span>并发槽位</span><strong>{{ worker.slots }}</strong></div>
          <em>{{ worker.state }}</em>
        </div>
      </section>

      <section class="asset-card contract-card">
        <header><div><h2>API 契约</h2><p>幂等提交、可查询、可取消、可追溯</p></div><span>v1</span></header>
        <dl>
          <div><dt>提交</dt><dd>POST /api/v1/assets/uv/unwrap</dd></div>
          <div><dt>状态</dt><dd>GET /api/v1/assets/jobs/{id}</dd></div>
          <div><dt>取消</dt><dd>POST /api/v1/assets/jobs/{id}/cancel</dd></div>
        </dl>
      </section>
    </div>

    <section class="asset-card delivery-card">
      <header><div><h2>最终交付物</h2><p>QA 存在硬失败时整批拒绝发布，杜绝中间结果误传</p></div><span>4 / 4</span></header>
      <div class="artifact-grid">
        <div v-for="(artifact, index) in artifacts" :key="artifact"><b>0{{ index + 1 }}</b><span>{{ artifact }}</span><small>SHA-256 校验</small></div>
      </div>
    </section>
  </div>
</template>
