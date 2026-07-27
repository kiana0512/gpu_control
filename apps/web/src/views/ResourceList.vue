<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import { useAutoRefresh } from "../composables/useAutoRefresh";

type Row = Record<string, unknown>;

const props = defineProps<{ title: string; kind: string }>();
const rows = ref<Row[]>([]);
const error = ref("");
const workflowFile = ref<HTMLInputElement>();
const clientDialogOpen = ref(false);
const editingClient = ref<Row | null>(null);
const accessPanelOpen = ref(false);
const accessClient = ref<Row | null>(null);
const codeTab = ref<"curl" | "python">("curl");
const selectedService = ref<"imageclip-rgba" | "modelview-inpaint">(
  "imageclip-rgba",
);
const search = ref("");
const clientScope = ref<"production" | "test">("production");

const clientForm = reactive({
  id: "",
  name: "",
  client_kind: "production" as "production" | "test",
  max_queued: 20,
  max_running: 1,
  daily_quota: 100,
  weight: 1,
  allowed_ips: "",
  callback_hosts: "",
  enabled: true,
});

const columnLabels: Record<string, string> = {
  id: "ID",
  status: "状态",
  severity: "级别",
  name: "名称",
  summary: "摘要",
  actor_id: "操作人",
  action: "操作",
  target_type: "对象类型",
  target_id: "对象 ID",
  result: "结果",
  created_at: "创建时间",
  updated_at: "更新时间",
  starts_at: "开始时间",
  ends_at: "结束时间",
};

async function load() {
  error.value = "";
  try {
    if (props.kind === "audit")
      rows.value = (await api.audits()) as unknown as Row[];
    else if (props.kind === "workflows") rows.value = await api.workflows();
    else if (props.kind === "clients") rows.value = await api.clients();
    else if (props.kind === "alerts") rows.value = await api.alerts();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "加载失败";
    throw cause;
  }
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
watch(
  () => props.kind,
  () => void run(),
);

const visibleRows = computed(() => {
  const source =
    props.kind === "clients"
      ? rows.value.filter(
          (row) =>
            row.role === "client" && row.client_kind === clientScope.value,
        )
      : rows.value;
  const keyword = search.value.trim().toLowerCase();
  if (!keyword) return source;
  return source.filter((row) =>
    JSON.stringify(row).toLowerCase().includes(keyword),
  );
});

const publicBase = computed(() => window.location.origin);
const serviceUrl = computed(
  () => `${publicBase.value}/api/v1/services/${selectedService.value}`,
);
const outputName = computed(() =>
  selectedService.value === "imageclip-rgba"
    ? "result-rgba.png"
    : "result-inpaint.png",
);
const curlExample = computed(() =>
  [
    `curl -X POST '${serviceUrl.value}' \\`,
    "  -H 'Idempotency-Key: order-001-attempt-1' \\",
    "  -F 'image=@input.png' \\",
    `  --output '${outputName.value}'`,
  ].join("\n"),
);
const pythonExample = computed(
  () =>
    `import requests\n\nwith open("input.png", "rb") as source:\n    response = requests.post(\n        "${serviceUrl.value}",\n        headers={"Idempotency-Key": "order-001-attempt-1"},\n        files={"image": ("input.png", source, "image/png")},\n        timeout=1900,\n    )\nresponse.raise_for_status()\nwith open("${outputName.value}", "wb") as output:\n    output.write(response.content)\nprint("job:", response.headers.get("X-Job-ID"))`,
);

function resetClientForm() {
  Object.assign(clientForm, {
    id: "",
    name: "",
    client_kind: "production",
    max_queued: 20,
    max_running: 1,
    daily_quota: 100,
    weight: 1,
    allowed_ips: "",
    callback_hosts: "",
    enabled: true,
  });
  editingClient.value = null;
}

async function createClient() {
  if (!editingClient.value && !/^[a-zA-Z0-9_-]+$/.test(clientForm.id)) {
    ElMessage.error("客户 ID 只能包含字母、数字、下划线和短横线");
    return;
  }
  if (!clientForm.name.trim()) {
    ElMessage.error("请输入客户显示名称");
    return;
  }
  try {
    const body = {
      ...clientForm,
      allowed_ips: clientForm.allowed_ips
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      callback_hosts: clientForm.callback_hosts
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    };
    if (editingClient.value) {
      await api.updateClient(String(editingClient.value.id), {
        name: body.name,
        enabled: body.enabled,
        max_queued: body.max_queued,
        max_running: body.max_running,
        daily_quota: body.daily_quota,
        weight: body.weight,
        allowed_ips: body.allowed_ips,
        callback_hosts: body.callback_hosts,
        reason: "管理员从控制台更新客户访问策略",
        confirm: true,
      });
    } else {
      await api.createClient(body);
    }
    clientDialogOpen.value = false;
    ElMessage.success(
      editingClient.value
        ? "客户设置已保存"
        : "客户已创建；未填写来源 IP 时仍保持默认开放和自动发现",
    );
    resetClientForm();
    await run();
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : "创建失败");
  }
}

function editClient(row: Row) {
  editingClient.value = row;
  Object.assign(clientForm, {
    id: String(row.id ?? ""),
    name: String(row.name ?? ""),
    client_kind:
      row.client_kind === "test" ? ("test" as const) : ("production" as const),
    max_queued: Number(row.max_queued ?? 20),
    max_running: Number(row.max_running ?? 1),
    daily_quota: Number(row.daily_quota ?? 1000),
    weight: Number(row.weight ?? 1),
    allowed_ips: Array.isArray(row.allowed_ips)
      ? row.allowed_ips.join(", ")
      : "",
    callback_hosts: Array.isArray(row.callback_hosts)
      ? row.callback_hosts.join(", ")
      : "",
    enabled: Boolean(row.enabled),
  });
  clientDialogOpen.value = true;
}

function showAccess(row: Row) {
  accessClient.value = row;
  accessPanelOpen.value = true;
}

function showPublicAccess() {
  accessClient.value = {
    name: "公共图片服务",
    last_seen_ip: "按真实来源 IP 自动识别",
  };
  accessPanelOpen.value = true;
}

async function copy(value: string, label: string) {
  if (window.navigator.clipboard && window.isSecureContext) {
    await window.navigator.clipboard.writeText(value);
  } else {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  ElMessage.success(`${label}已复制`);
}

async function toggle(row: Row) {
  await ElMessageBox.confirm(
    `确认${row.enabled ? "停用" : "启用"} ${row.workflow_key}:${row.version}？`,
    "工作流状态变更",
    { type: "warning" },
  );
  await api.enableWorkflow(Number(row.id), !row.enabled);
  ElMessage.success("工作流状态已更新");
  await run();
}

async function testFeishu() {
  const result = await api.testFeishu();
  ElMessage.success(
    result.sent ? "测试消息已发送" : "飞书未配置，系统仍可正常运行",
  );
}

async function importWorkflow(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  try {
    const payload = JSON.parse(await file.text()) as Row;
    await ElMessageBox.confirm(
      `确认导入 ${String(payload.workflow_key ?? "未知工作流")}:${String(payload.version ?? "?")}？`,
      "导入 API 工作流包",
      { type: "warning" },
    );
    await api.importWorkflow(payload);
    ElMessage.success("工作流已导入；确认兼容性后再启用");
    await run();
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : "导入失败");
  } finally {
    input.value = "";
  }
}

function formatUpdateTime() {
  return (
    lastUpdatedAt.value?.toLocaleTimeString("zh-CN", { hour12: false }) ??
    "等待首次同步"
  );
}

function columnLabel(key: string) {
  return columnLabels[key] ?? key.replaceAll("_", " ");
}

function formatCell(value: unknown) {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value))
    return new Date(value).toLocaleString("zh-CN", { hour12: false });
  return String(value);
}
</script>

<template>
  <div class="page resource-page">
    <div class="page-heading resource-heading">
      <div>
        <h1>{{ title }}</h1>
        <p v-if="kind === 'workflows'">
          管理真实 ComfyUI API 工作流、版本与运行要求
        </p>
        <p v-else-if="kind === 'clients'">
          按来源 IP 自动发现调用方，并管理配额与访问策略
        </p>
        <p v-else>配置与操作均通过管理 API 保存并写入审计日志</p>
      </div>
      <div class="heading-actions">
        <span class="refresh-state"
          ><i :class="{ spinning: refreshing }"></i>自动刷新 · 10 秒<br /><small
            >最后更新 {{ formatUpdateTime() }}</small
          ></span
        >
        <template v-if="kind === 'clients'">
          <button class="secondary" @click="showPublicAccess">
            查看图片 API
          </button>
          <button
            class="primary"
            @click="
              resetClientForm();
              clientDialogOpen = true;
            "
          >
            预配置客户
          </button>
        </template>
        <button
          v-else-if="kind === 'workflows'"
          class="primary"
          @click="workflowFile?.click()"
        >
          导入工作流包
        </button>
        <button
          v-else-if="kind === 'alerts'"
          class="secondary"
          @click="testFeishu"
        >
          测试飞书
        </button>
        <button v-else class="secondary" :disabled="refreshing" @click="run">
          立即刷新
        </button>
      </div>
      <input
        v-if="kind === 'workflows'"
        ref="workflowFile"
        type="file"
        accept="application/json,.json"
        hidden
        @change="importWorkflow"
      />
    </div>

    <section v-if="kind === 'clients'" class="onboarding-strip">
      <div>
        <b>1</b
        ><span
          ><strong>软件直接调用统一 API</strong
          ><small>无需 API Key，默认允许新来源 IP</small></span
        >
      </div>
      <div>
        <b>2</b
        ><span
          ><strong>系统按真实 IP 自动建档</strong
          ><small>不同 IP 独立限流、排队和并发</small></span
        >
      </div>
      <div>
        <b>3</b
        ><span
          ><strong>管理员按需限制</strong
          ><small>可选预配置 IP 和客户配额</small></span
        >
      </div>
    </section>

    <div v-if="error" class="error-banner persistent-error">
      <strong>数据同步暂时失败</strong><span>{{ error }}</span
      ><button @click="run">立即重试</button>
    </div>

    <section class="resource-card">
      <div
        v-if="kind === 'workflows' || kind === 'clients'"
        class="resource-toolbar"
      >
        <div class="search-field">
          <span>⌕</span
          ><input
            v-model="search"
            :placeholder="
              kind === 'clients' ? '搜索客户、IP 或 ID' : '搜索工作流或版本'
            "
          />
        </div>
        <div v-if="kind === 'clients'" class="scope-tabs">
          <button
            :class="{ active: clientScope === 'production' }"
            @click="clientScope = 'production'"
          >
            真实客户
          </button>
          <button
            :class="{ active: clientScope === 'test' }"
            @click="clientScope = 'test'"
          >
            测试客户
          </button>
        </div>
        <span class="record-count">{{ visibleRows.length }} 条记录</span>
      </div>

      <template v-if="kind === 'workflows'"
        ><div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>工作流</th>
                <th>版本</th>
                <th>状态</th>
                <th>最低显存</th>
                <th>超时</th>
                <th>模型 / 节点要求</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in visibleRows" :key="String(row.id)">
                <td>
                  <strong class="table-primary">{{ row.workflow_key }}</strong
                  ><small class="table-secondary">ID {{ row.id }}</small>
                </td>
                <td>
                  <code>{{ row.version }}</code>
                </td>
                <td>
                  <span class="state-pill" :class="{ enabled: row.enabled }">{{
                    row.enabled ? "已启用" : "已停用"
                  }}</span>
                </td>
                <td>{{ (Number(row.min_vram_mb) / 1024).toFixed(1) }} GB</td>
                <td>{{ Math.round(Number(row.timeout_seconds) / 60) }} 分钟</td>
                <td>
                  <span class="requirement-count"
                    >{{
                      (row.required_models as unknown[]).length
                    }}
                    个模型</span
                  ><span class="requirement-count"
                    >{{
                      (row.required_custom_nodes as unknown[]).length
                    }}
                    个节点</span
                  >
                </td>
                <td>
                  <button class="table-action" @click="toggle(row)">
                    {{ row.enabled ? "停用" : "启用" }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div></template
      >

      <template v-else-if="kind === 'clients'"
        ><div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>客户 / 来源 IP</th>
                <th>类型</th>
                <th>状态</th>
                <th>最多排队</th>
                <th>最大并发</th>
                <th>每日配额</th>
                <th>最后访问</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in visibleRows" :key="String(row.id)">
                <td>
                  <strong class="table-primary">{{ row.name }}</strong
                  ><small class="table-secondary">{{
                    row.last_seen_ip ||
                    (row.allowed_ips as string[]).join(", ") ||
                    "等待首次访问"
                  }}</small>
                </td>
                <td>
                  <span
                    class="client-kind-pill"
                    :class="{ test: row.client_kind === 'test' }"
                    >{{
                      row.client_kind === "test" ? "压力测试" : "真实业务"
                    }}</span
                  >
                </td>
                <td>
                  <span class="state-pill" :class="{ enabled: row.enabled }">{{
                    row.enabled ? "启用中" : "已停用"
                  }}</span>
                </td>
                <td>{{ row.max_queued }}</td>
                <td>{{ row.max_running }}</td>
                <td>{{ row.daily_quota }}</td>
                <td>{{ formatCell(row.last_seen_at) }}</td>
                <td>
                  <div class="client-row-actions">
                    <button class="table-action" @click="editClient(row)">
                      管理设置</button
                    ><button
                      class="table-action accent"
                      @click="showAccess(row)"
                    >
                      调用方式
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div></template
      >

      <template v-else
        ><div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th
                  v-for="keyName in Object.keys(visibleRows[0] ?? {}).slice(
                    0,
                    7,
                  )"
                  :key="keyName"
                >
                  {{ columnLabel(keyName) }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, index) in visibleRows"
                :key="String(row.id ?? index)"
              >
                <td
                  v-for="keyName in Object.keys(visibleRows[0] ?? {}).slice(
                    0,
                    7,
                  )"
                  :key="keyName"
                >
                  <code v-if="typeof row[keyName] === 'object'">{{
                    formatCell(row[keyName])
                  }}</code
                  ><span v-else>{{ formatCell(row[keyName]) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div></template
      >

      <div
        v-if="!visibleRows.length && !refreshing"
        class="empty-state action-empty"
      >
        <div class="empty-icon">＋</div>
        <strong>{{
          kind === "clients" ? "等待第一个软件调用" : "暂无真实记录"
        }}</strong
        ><span>{{
          kind === "clients"
            ? "无需预先创建客户。新的来源 IP 调用图片服务后会自动显示在这里。"
            : "当前数据库中没有可显示的记录。"
        }}</span>
      </div>
    </section>

    <div
      v-if="clientDialogOpen"
      class="modal-backdrop"
      @click.self="clientDialogOpen = false"
    >
      <section class="form-modal" role="dialog" aria-modal="true">
        <header>
          <div>
            <h2>{{ editingClient ? "管理 API 客户" : "预配置 API 客户" }}</h2>
            <p>
              {{
                editingClient
                  ? `${editingClient.name} · ${editingClient.last_seen_ip || editingClient.id}`
                  : "这一步可选；未知 IP 默认允许并会在首次调用时自动建档。"
              }}
            </p>
          </div>
          <button class="icon-button" @click="clientDialogOpen = false">
            ×
          </button>
        </header>
        <div class="capacity-note">
          <strong>当前集群：3 台可用 GPU</strong
          ><span
            >真实客户按业务 SLA 配额；测试客户建议单客户并发
            1，通过多个测试客户验证公平调度。</span
          >
        </div>
        <div class="form-grid">
          <label
            ><span>客户 ID *</span
            ><input
              v-model.trim="clientForm.id"
              :disabled="Boolean(editingClient)"
              placeholder="例如 inpaint_01"
            /><small>内部唯一标识；创建后不可修改。</small></label
          ><label
            ><span>客户类型 *</span
            ><select v-model="clientForm.client_kind">
              <option value="production">真实业务</option>
              <option value="test">压力测试</option>
            </select>
            <small
              >测试客户只使用真实业务空闲槽，且不会进入真实统计。</small
            ></label
          ><label
            ><span>显示名称 *</span
            ><input
              v-model.trim="clientForm.name"
              placeholder="例如 局部重绘业务"
            /><small>用于管理台展示，可以使用中文。</small></label
          ><label
            ><span>最多排队</span
            ><input
              v-model.number="clientForm.max_queued"
              type="number"
              min="1"
              max="10000"
            /><small>该来源允许等待的任务数，当前建议 20。</small></label
          ><label
            ><span>最大并发</span
            ><input
              v-model.number="clientForm.max_running"
              type="number"
              min="1"
              max="10"
            /><small>当前只有 1 台 GPU，请填 1。</small></label
          ><label
            ><span>每日配额</span
            ><input
              v-model.number="clientForm.daily_quota"
              type="number"
              min="1"
              max="1000000"
            /><small>测试建议 100，正式按业务量调整。</small></label
          ><label
            ><span>调度权重</span
            ><input
              v-model.number="clientForm.weight"
              type="number"
              min="1"
              max="100"
            /><small>普通客户统一填 1。</small></label
          ><label class="full"
            ><span>固定来源 IP（可选）</span
            ><input
              v-model.trim="clientForm.allowed_ips"
              placeholder="例如 10.3.34.21；多个 IP 用英文逗号分隔"
            /><small
              >留空表示不预绑定；首次访问仍会自动识别和建档。</small
            ></label
          ><label class="full"
            ><span>回调域名（可选）</span
            ><input
              v-model.trim="clientForm.callback_hosts"
              placeholder="例如 api.example.com"
            /><small>只有需要接收异步完成通知时填写。</small></label
          ><label v-if="editingClient" class="full enabled-control"
            ><input v-model="clientForm.enabled" type="checkbox" /><span
              ><strong>允许该来源继续提交任务</strong
              ><small
                >关闭后该客户将立即停止接收新任务，历史任务不受影响。</small
              ></span
            ></label
          >
        </div>
        <footer>
          <button class="secondary" @click="clientDialogOpen = false">
            取消</button
          ><button class="primary" @click="createClient">
            {{ editingClient ? "保存设置" : "创建客户" }}
          </button>
        </footer>
      </section>
    </div>

    <div
      v-if="accessPanelOpen"
      class="panel-backdrop"
      @click.self="accessPanelOpen = false"
    >
      <aside class="key-panel">
        <header>
          <div>
            <h2>图片服务调用方式</h2>
            <p>
              {{ accessClient?.name }} ·
              {{ accessClient?.last_seen_ip || "预配置客户" }}
            </p>
          </div>
          <button class="icon-button" @click="accessPanelOpen = false">
            ×
          </button>
        </header>
        <div class="access-notice">
          <strong>无需 API Key</strong
          ><span
            >系统根据真实来源 IP 自动识别客户。上传的是图片，HTTP
            成功响应体也是最终图片。</span
          >
        </div>
        <section class="usage-guide">
          <h3>选择服务</h3>
          <div class="service-tabs">
            <button
              :class="{ active: selectedService === 'imageclip-rgba' }"
              @click="selectedService = 'imageclip-rgba'"
            >
              ImageClip 抠图（RGBA）</button
            ><button
              :class="{ active: selectedService === 'modelview-inpaint' }"
              @click="selectedService = 'modelview-inpaint'"
            >
              ModelView 局部重绘
            </button>
          </div>
          <ol>
            <li>
              统一入口：<code>{{ serviceUrl }}</code>
            </li>
            <li>multipart 图片字段固定为 <code>image</code>。</li>
            <li>响应头 <code>X-Job-ID</code> 可用于日志和排障。</li>
          </ol>
          <div class="code-tabs">
            <button
              :class="{ active: codeTab === 'curl' }"
              @click="codeTab = 'curl'"
            >
              cURL</button
            ><button
              :class="{ active: codeTab === 'python' }"
              @click="codeTab = 'python'"
            >
              Python
            </button>
          </div>
          <pre><code>{{ codeTab === 'curl' ? curlExample : pythonExample }}</code><button @click="copy(codeTab === 'curl' ? curlExample : pythonExample, '调用示例')">复制示例</button></pre>
        </section>
        <footer>
          <span>客户端超时建议设置为 1900 秒</span
          ><button class="primary" @click="accessPanelOpen = false">
            关闭
          </button>
        </footer>
      </aside>
    </div>
  </div>
</template>
