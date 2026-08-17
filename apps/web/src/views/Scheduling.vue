<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import type { AssetProcessingOverview, NodeInfo } from "../types";
import { useAutoRefresh } from "../composables/useAutoRefresh";

type SchedulingForm = {
  overflow_4090_auto_enabled: boolean;
  overflow_queue_threshold: number;
  overflow_wait_threshold_seconds: number;
  overflow_4090_min_free_vram_mb: number;
  overflow_4090_max_gpu_util_percent: number;
  overflow_4090_allowed_windows: string;
};

type WorkflowRow = {
  workflow_key: string;
  version: string;
  enabled: boolean;
  min_vram_mb: number;
  timeout_seconds: number;
};

const form = reactive<SchedulingForm>({
  overflow_4090_auto_enabled: false,
  overflow_queue_threshold: 20,
  overflow_wait_threshold_seconds: 120,
  overflow_4090_min_free_vram_mb: 20000,
  overflow_4090_max_gpu_util_percent: 20,
  overflow_4090_allowed_windows: "",
});
const saved = ref<SchedulingForm>({ ...form });
const nodes = ref<NodeInfo[]>([]);
const workflows = ref<WorkflowRow[]>([]);
const assetOverview = ref<AssetProcessingOverview | null>(null);
const error = ref("");
const saving = ref(false);

const changed = computed(
  () => JSON.stringify(form) !== JSON.stringify(saved.value),
);
const orderedNodes = computed(() =>
  [...nodes.value].sort((left, right) => {
    const order = [
      "control-4090",
      "worker-3090-a",
      "worker-3090-b",
      "worker-4070ti-animation-host-01",
    ];
    const leftIndex = order.indexOf(left.id);
    const rightIndex = order.indexOf(right.id);
    return (
      (leftIndex === -1 ? order.length : leftIndex) -
        (rightIndex === -1 ? order.length : rightIndex) ||
      left.display_name.localeCompare(right.display_name, "zh-CN")
    );
  }),
);
const activeNodes = computed(() =>
  orderedNodes.value.filter(
    (node) => node.health === "ONLINE" && node.mode === "ACTIVE",
  ),
);
const totalSlots = computed(() =>
  nodes.value.reduce((total, node) => total + node.max_concurrency, 0),
);
const activeSlots = computed(() =>
  activeNodes.value.reduce((total, node) => total + node.max_concurrency, 0),
);
const usedSlots = computed(() =>
  activeNodes.value.reduce((total, node) => total + node.current_jobs, 0),
);
const enabledWorkflows = computed(() =>
  workflows.value.filter((workflow) => workflow.enabled),
);
const clusterMode = computed(() => {
  if (!nodes.value.length) return "等待节点状态";
  if (activeNodes.value.length === nodes.value.length)
    return `${nodes.value.length} 节点并行（${activeSlots.value} 个 GPU 槽位）`;
  return `降级运行（${activeNodes.value.length}/${nodes.value.length} 节点可接单）`;
});
const assetWorkers = computed(() => assetOverview.value?.workers ?? []);
const onlineAssetWorkers = computed(() =>
  assetWorkers.value.filter((worker) => worker.status === "ONLINE"),
);
const assetActiveJobs = computed(() => {
  const counts = assetOverview.value?.summary.counts ?? {};
  return (counts.QUEUED ?? 0) + (counts.CLAIMED ?? 0) + (counts.RUNNING ?? 0);
});
const assetTotalSlots = computed(() =>
  onlineAssetWorkers.value.reduce(
    (total, worker) => total + worker.max_concurrency,
    0,
  ),
);
const assetUsedSlots = computed(() =>
  onlineAssetWorkers.value.reduce(
    (total, worker) => total + worker.current_jobs,
    0,
  ),
);
const availableGpuSlots = computed(() =>
  Math.max(0, activeSlots.value - usedSlots.value),
);
const foreignQueueNodes = computed(() =>
  nodes.value.filter((node) => node.foreign_queue_detected),
);
const schedulingRisks = computed(() => {
  const risks: Array<{
    tone: "danger" | "warning" | "healthy";
    title: string;
    detail: string;
  }> = [];
  if (!nodes.value.length) {
    risks.push({
      tone: "danger",
      title: "尚未读取到 GPU 节点",
      detail: "新任务会继续留在队列，直到节点恢复心跳并满足兼容性检查。",
    });
  } else if (!activeNodes.value.length) {
    risks.push({
      tone: "danger",
      title: "当前没有可接单 GPU 节点",
      detail:
        "节点可能处于离线、排空、保留或停用状态；运行中的任务不会被页面强制迁移。",
    });
  } else if (activeNodes.value.length < nodes.value.length) {
    risks.push({
      tone: "warning",
      title: `${nodes.value.length - activeNodes.value.length} 个 GPU 节点未参与接单`,
      detail:
        "调度器仍会在剩余兼容节点间继续分配，不会因首个节点不可用阻塞整批任务。",
    });
  }
  if (foreignQueueNodes.value.length) {
    risks.push({
      tone: "warning",
      title: `${foreignQueueNodes.value.length} 个节点检测到外部队列`,
      detail:
        "外部 ComfyUI 任务可能占用显存或执行槽；调度器会按节点上报状态保护新任务。",
    });
  }
  if (!enabledWorkflows.value.length) {
    risks.push({
      tone: "danger",
      title: "没有已启用的工作流版本",
      detail: "所有 GPU 新任务都会因工作流门禁无法领取。",
    });
  }
  if (!risks.length) {
    risks.push({
      tone: "healthy",
      title: "当前未发现调度容量风险",
      detail: "在线节点、执行槽与工作流门禁均有可用路径。",
    });
  }
  return risks;
});
const overflowState = computed(() =>
  form.overflow_4090_auto_enabled ? "自动备用已开启" : "自动备用已关闭",
);
const freeVramGb = computed({
  get: () => Number((form.overflow_4090_min_free_vram_mb / 1024).toFixed(1)),
  set: (value: number) => {
    form.overflow_4090_min_free_vram_mb = Math.round(Number(value || 0) * 1024);
  },
});

async function load() {
  error.value = "";
  try {
    const [settings, nodeRows, workflowRows, assetRows] = await Promise.all([
      api.settings(),
      api.nodes(),
      api.workflows(),
      api.assetProcessing(),
    ]);
    Object.assign(form, settings);
    saved.value = { ...form };
    nodes.value = nodeRows;
    workflows.value = workflowRows as unknown as WorkflowRow[];
    assetOverview.value = assetRows;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "调度策略加载失败";
    throw cause;
  }
}

async function save() {
  if (!changed.value) return;
  await ElMessageBox.confirm(
    "确认把这些条件应用到后续新任务吗？正在运行的任务不受影响，保存操作会进入审计记录。",
    "确认更新调度条件",
    { type: "warning" },
  );
  saving.value = true;
  try {
    const keys = Object.keys(form) as (keyof SchedulingForm)[];
    for (const key of keys) {
      if (form[key] !== saved.value[key])
        await api.updateSetting(key, form[key]);
    }
    saved.value = { ...form };
    ElMessage.success("调度策略已保存");
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : "保存失败");
  } finally {
    saving.value = false;
  }
}

function reset() {
  Object.assign(form, saved.value);
}

function nodeState(node: NodeInfo) {
  if (node.health !== "ONLINE")
    return node.health === "DEGRADED" ? "异常" : "离线";
  if (node.mode === "ACTIVE") return node.current_jobs ? "执行中" : "可接单";
  if (node.mode === "DRAINING") return "排空中";
  if (node.mode === "RESERVED") return "已暂停";
  if (node.mode === "OVERFLOW") return "备用";
  return "已停用";
}

function workflowName(key: string) {
  if (key === "imageclip-rgba") return "ImageClip 抠图";
  if (key === "modelview-inpaint") return "ModelView 局部重绘";
  if (key === "modelview-roughness") return "PBR 粗糙度";
  return key;
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page scheduling-page schedule-redesign">
    <div class="page-heading">
      <div>
        <h1>调度运行说明</h1>
        <p>
          看清任务如何分配、当前还有多少容量，以及哪些高级条件会影响新任务。
        </p>
      </div>
      <div class="heading-actions">
        <span class="refresh-state"
          ><i :class="{ spinning: refreshing }"></i>自动刷新 · 10 秒<br /><small
            >最后更新
            {{
              lastUpdatedAt?.toLocaleTimeString("zh-CN", { hour12: false }) ??
              "等待首次同步"
            }}</small
          ></span
        >
        <button type="button" class="secondary" @click="run">
          {{ refreshing ? "刷新中…" : "立即刷新" }}
        </button>
      </div>
    </div>

    <div v-if="error" class="error-banner persistent-error">
      <strong>调度状态加载失败</strong><span>{{ error }}</span
      ><button type="button" @click="run">重试</button>
    </div>

    <section class="dispatch-explainer">
      <header>
        <div>
          <h2>一个新任务会怎样被分配</h2>
          <p>下面四步由后端调度器自动完成，关闭浏览器也不会中断。</p>
        </div>
        <span class="rules-live">规则已生效</span>
      </header>
      <ol class="dispatch-flow">
        <li>
          <b>1</b>
          <div>
            <strong>记录任务与客户身份</strong
            ><span>真实业务与测试任务可独立筛选；本页不会发起压测。</span>
          </div>
        </li>
        <li>
          <b>2</b>
          <div>
            <strong>筛掉不兼容节点</strong
            ><span>核对工作流版本、插件、模型与显存。</span>
          </div>
        </li>
        <li>
          <b>3</b>
          <div>
            <strong>尝试可用节点</strong
            ><span>首个节点不适配时继续尝试其它候选。</span>
          </div>
        </li>
        <li>
          <b>4</b>
          <div>
            <strong>领取并持续审计</strong
            ><span>租约、防重复、重试和执行证据由后端保存。</span>
          </div>
        </li>
      </ol>
      <div class="dispatch-safety-note">
        <strong>页面只负责展示与配置</strong>
        <span
          >刷新、切换页面或关闭浏览器都不会改变正在运行的任务；保存高级条件只影响后续新任务。</span
        >
      </div>
    </section>

    <section class="capacity-overview">
      <header>
        <div>
          <h2>实时容量</h2>
          <p>
            {{
              clusterMode
            }}；节点数和槽位均来自实时心跳，不使用固定写死的数据。
          </p>
        </div>
        <span
          class="cluster-health"
          :class="{ degraded: activeNodes.length !== nodes.length }"
        >
          {{ activeNodes.length }} / {{ nodes.length }} GPU 节点可接单
        </span>
      </header>

      <div class="capacity-metrics">
        <article>
          <span>GPU 空闲槽位</span>
          <strong
            >{{ availableGpuSlots }} / {{ activeSlots || totalSlots }}</strong
          >
          <small>{{ usedSlots }} 个槽位正在执行</small>
        </article>
        <article>
          <span>已启用工作流</span>
          <strong>{{ enabledWorkflows.length }}</strong>
          <small>只有身份匹配的节点可领取</small>
        </article>
        <article>
          <span>CPU Asset 槽位</span>
          <strong>{{ assetUsedSlots }} / {{ assetTotalSlots }}</strong>
          <small>{{ assetActiveJobs }} 个资产任务处理中</small>
        </article>
        <article>
          <span>4090 备用条件</span>
          <strong>{{
            form.overflow_4090_auto_enabled ? "开启" : "关闭"
          }}</strong>
          <small>仅节点处于 OVERFLOW 时生效</small>
        </article>
      </div>

      <div class="capacity-lanes">
        <section>
          <div class="capacity-lane-title">
            <div>
              <strong>GPU 推理节点</strong
              ><span>执行 ImageClip / ModelView 工作流</span>
            </div>
            <router-link to="/nodes">管理节点 →</router-link>
          </div>
          <ul class="capacity-node-list">
            <li v-for="node in orderedNodes" :key="node.id">
              <div class="capacity-node-name">
                <i :class="node.health.toLowerCase()"></i>
                <div>
                  <strong>{{ node.display_name }}</strong
                  ><span>{{ node.id }}</span>
                </div>
              </div>
              <dl>
                <div>
                  <dt>接单状态</dt>
                  <dd>{{ nodeState(node) }}</dd>
                </div>
                <div>
                  <dt>槽位</dt>
                  <dd>{{ node.current_jobs }} / {{ node.max_concurrency }}</dd>
                </div>
                <div>
                  <dt>空闲显存</dt>
                  <dd>{{ (node.free_vram_mb / 1024).toFixed(1) }} GB</dd>
                </div>
              </dl>
            </li>
            <li v-if="!orderedNodes.length" class="capacity-empty">
              尚无 GPU 节点心跳，调度器会保留队列等待恢复。
            </li>
          </ul>
        </section>

        <section>
          <div class="capacity-lane-title">
            <div>
              <strong>CPU Asset Worker</strong
              ><span
                >独立处理 UV、重拓扑；Substance 烘焙单独占用 3090-B GPU</span
              >
            </div>
            <router-link to="/asset-processing">查看资产任务 →</router-link>
          </div>
          <ul class="capacity-node-list">
            <li v-for="worker in assetWorkers" :key="worker.id">
              <div class="capacity-node-name">
                <i
                  :class="worker.status === 'ONLINE' ? 'online' : 'offline'"
                ></i>
                <div>
                  <strong>{{ worker.display_name }}</strong
                  ><span>{{ worker.node_id }}</span>
                </div>
              </div>
              <dl>
                <div>
                  <dt>接单状态</dt>
                  <dd>
                    {{ worker.status === "ONLINE" ? "可接单" : "心跳离线" }}
                  </dd>
                </div>
                <div>
                  <dt>槽位</dt>
                  <dd>
                    {{ worker.current_jobs }} / {{ worker.max_concurrency }}
                  </dd>
                </div>
                <div>
                  <dt>运行时</dt>
                  <dd>Blender {{ worker.blender_version }}</dd>
                </div>
              </dl>
            </li>
            <li v-if="!assetWorkers.length" class="capacity-empty">
              尚无 Asset Worker 心跳；CPU 资产队列与 GPU 推理队列互不占用。
            </li>
          </ul>
        </section>
      </div>
    </section>

    <div class="schedule-insight-grid">
      <section class="rule-ledger-section">
        <header>
          <h2>当前分配规则</h2>
          <p>白话说明每条规则影响谁，以及控制台目前读到的状态。</p>
        </header>
        <ol class="rule-ledger">
          <li>
            <b>01</b>
            <div>
              <strong>兼容性门禁优先</strong>
              <p>版本、模型、插件或显存不满足时，节点不会领取任务。</p>
            </div>
            <dl>
              <dt>当前值</dt>
              <dd>{{ enabledWorkflows.length }} 个工作流版本启用</dd>
              <dt>影响</dt>
              <dd>所有 GPU 新任务</dd>
            </dl>
          </li>
          <li>
            <b>02</b>
            <div>
              <strong>候选节点逐个尝试</strong>
              <p>一个候选不兼容不会挡住其它兼容节点，批次可继续分散执行。</p>
            </div>
            <dl>
              <dt>当前值</dt>
              <dd>{{ activeNodes.length }} 个节点可参与</dd>
              <dt>影响</dt>
              <dd>GPU 节点选择</dd>
            </dl>
          </li>
          <li>
            <b>03</b>
            <div>
              <strong>生产 / 测试范围可追溯</strong>
              <p>
                客户身份用于独立查看和容量门禁；压测流量必须由外部执行门禁确认空闲窗口后放行。
              </p>
            </div>
            <dl>
              <dt>当前值</dt>
              <dd>生产 / 测试身份分层</dd>
              <dt>影响</dt>
              <dd>展示范围与压测准入</dd>
            </dl>
          </li>
          <li>
            <b>04</b>
            <div>
              <strong>租约、防重复与恢复</strong>
              <p>数据库领取、持久化提交意图和重试记录共同避免重复执行。</p>
            </div>
            <dl>
              <dt>当前值</dt>
              <dd>后端自动执行</dd>
              <dt>影响</dt>
              <dd>失败与重启恢复</dd>
            </dl>
          </li>
          <li>
            <b>05</b>
            <div>
              <strong>4090：局部重绘首选 + 三台 24 GiB 扩展</strong>
              <p>
                局部重绘只在 4090、3090-A、3090-B 上执行；发生抠图冲突时，
                4090 的抠图帧安全中断并改派其它物理 GPU，清显存后
                优先响应局部重绘；没有局部重绘排队时，4090 继续接兼容普通任务。
                4070Ti 因 12 GiB 显存被兼容表硬排除。
              </p>
            </div>
            <dl>
              <dt>保护窗口</dt>
              <dd>新局部重绘到达后 10 分钟，可续期且硬过期</dd>
              <dt>影响</dt>
              <dd>仅 4090 GPU 单槽优先级；空闲时不阻塞普通任务，3090 作为兼容回退</dd>
            </dl>
          </li>
          <li>
            <b>06</b>
            <div>
              <strong>3090-B：唯一 Substance 烘焙通道</strong>
              <p>
                生产烘焙排队后停止领取新抠图；当前抠图帧必须自然完成，再清空模型缓存并切换
                Windows Baker。持续到达会续期保护。
              </p>
            </div>
            <dl>
              <dt>保护窗口</dt>
              <dd>
                最后一次新烘焙到达后 5 分钟；积压队列继续保留执行权，空闲时可由管理员立即解除
              </dd>
              <dt>影响</dt>
              <dd>仅 3090-B GPU 单槽</dd>
            </dl>
          </li>
          <li>
            <b>07</b>
            <div>
              <strong>正常状态自动恢复，CPU 不冻结</strong>
              <p>
                保护窗口不能被旧任务或心跳无限续期；到期后立即恢复抠图、粗糙度等普通
                GPU 调度。拓扑、拆 UV 等 CPU 槽始终独立运行。
              </p>
            </div>
            <dl>
              <dt>GPU 槽位</dt>
              <dd>每节点 1，避免模型并发 OOM</dd>
              <dt>CPU 槽位</dt>
              <dd>由各 Asset Worker 独立上报</dd>
            </dl>
          </li>
        </ol>
      </section>

      <aside class="schedule-risk-panel">
        <header>
          <h2>风险提示</h2>
          <p>只展示当前能从心跳与配置确认的风险。</p>
        </header>
        <ul>
          <li
            v-for="risk in schedulingRisks"
            :key="risk.title"
            :class="risk.tone"
          >
            <i></i>
            <div>
              <strong>{{ risk.title }}</strong
              ><span>{{ risk.detail }}</span>
            </div>
          </li>
        </ul>
      </aside>
    </div>

    <section class="workflow-identity-strip">
      <div>
        <strong>已启用工作流身份</strong
        ><span>节点只有在身份一致时才会接单。</span>
      </div>
      <div class="workflow-identities">
        <span
          v-for="workflow in enabledWorkflows"
          :key="`${workflow.workflow_key}:${workflow.version}`"
          >{{ workflowName(workflow.workflow_key)
          }}<b>{{ workflow.version }}</b></span
        >
        <span v-if="!enabledWorkflows.length" class="missing"
          >当前没有启用版本</span
        >
      </div>
    </section>

    <details class="advanced-policy">
      <summary>
        <div>
          <strong>4090 备用模式高级条件</strong>
          <span
            >仅当 4090 节点被运维切换为 OVERFLOW
            时，这些条件才参与新任务分配。</span
          >
        </div>
        <em>{{ overflowState }} · 展开配置</em>
      </summary>
      <div class="advanced-policy-content">
        <header>
          <div>
            <h2>是否允许自动启用 4090 备用容量</h2>
            <p>开启后仍需同时满足队列、时间、显存和利用率门槛。</p>
          </div>
          <label class="switch-control"
            ><input
              v-model="form.overflow_4090_auto_enabled"
              type="checkbox"
            /><span></span
            >{{ form.overflow_4090_auto_enabled ? "已开启" : "已关闭" }}</label
          >
        </header>
        <div class="policy-fields">
          <label
            ><span>排队任务达到</span>
            <div>
              <input
                v-model.number="form.overflow_queue_threshold"
                type="number"
                min="1"
              /><b>个</b>
            </div>
            <small>队列达到这个数量后，才允许备用 4090 接单。</small></label
          >
          <label
            ><span>最长等待超过</span>
            <div>
              <input
                v-model.number="form.overflow_wait_threshold_seconds"
                type="number"
                min="1"
              /><b>秒</b>
            </div>
            <small>队列不长但等待过久时，也可触发备用容量。</small></label
          >
          <label
            ><span>允许使用时间</span>
            <div>
              <input
                v-model.trim="form.overflow_4090_allowed_windows"
                placeholder="留空表示全天；例如 22:00-06:00"
              />
            </div>
            <small>多个时间段使用英文逗号分隔。</small></label
          >
        </div>
        <div class="advanced-divider">
          <strong>安全限制</strong
          ><span>防止 4090 正在被人工使用时被备用调度误占。</span>
        </div>
        <div class="policy-fields two-columns">
          <label
            ><span>至少保留空闲显存</span>
            <div>
              <input
                v-model.number="freeVramGb"
                type="number"
                min="0"
                step="0.5"
              /><b>GB</b>
            </div>
            <small>低于该值时不会自动分配任务。</small></label
          >
          <label
            ><span>GPU 利用率低于</span>
            <div>
              <input
                v-model.number="form.overflow_4090_max_gpu_util_percent"
                type="number"
                min="0"
                max="100"
              /><b>%</b>
            </div>
            <small>高于该值表示 GPU 正忙，不会自动接单。</small></label
          >
        </div>
      </div>
    </details>

    <div class="save-bar" :class="{ visible: changed }">
      <span>
        <strong>{{
          changed ? "有尚未保存的调度条件" : "所有调度条件已保存"
        }}</strong>
        <small>{{
          changed ? "保存后只影响后续新任务" : "运行中任务不受此页面影响"
        }}</small>
      </span>
      <div>
        <button
          type="button"
          class="secondary"
          :disabled="!changed || saving"
          @click="reset"
        >
          撤销修改
        </button>
        <button
          type="button"
          class="primary"
          :disabled="!changed || saving"
          @click="save"
        >
          {{ saving ? "保存中…" : "确认并保存" }}
        </button>
      </div>
    </div>
  </div>
</template>
