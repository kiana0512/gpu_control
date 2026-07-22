<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
const props = defineProps<{ title: string; kind: string }>();
const rows = ref<Record<string, unknown>[]>([]);
const settings = ref<Record<string, unknown>>({});
const loading = ref(false);
const error = ref("");
const logKey = ref("job_id");
const logValue = ref("");
const createdKey = ref("");
const workflowFile = ref<HTMLInputElement>();
async function load() {
  loading.value = true;
  error.value = "";
  try {
    if (props.kind === "audit")
      rows.value = (await api.audits()) as unknown as Record<string, unknown>[];
    else if (props.kind === "workflows") rows.value = await api.workflows();
    else if (props.kind === "clients") rows.value = await api.clients();
    else if (props.kind === "alerts") rows.value = await api.alerts();
    else if (props.kind === "scheduling" || props.kind === "settings")
      settings.value = await api.settings();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}
async function toggle(row: Record<string, unknown>) {
  await ElMessageBox.confirm(
    `确认${row.enabled ? "停用" : "启用"} ${row.workflow_key}:${row.version}？`,
    "工作流操作二次确认",
    { type: "warning" },
  );
  await api.enableWorkflow(Number(row.id), !row.enabled);
  ElMessage.success("工作流状态已更新");
  await load();
}
async function createClient() {
  const id = prompt("客户 ID（字母、数字、_、-）");
  if (!id) return;
  const name = prompt("客户显示名称") || id;
  await api.createClient({
    id,
    name,
    max_queued: 20,
    max_running: 1,
    daily_quota: 1000,
    weight: 1,
    callback_hosts: [],
  });
  ElMessage.success("客户已创建");
  await load();
}
async function key(row: Record<string, unknown>) {
  await ElMessageBox.confirm(
    `确认为客户 ${String(row.id)} 创建新的 API Key？旧 Key 不会自动失效。`,
    "API Key 二次确认",
    { type: "warning" },
  );
  const result = await api.createKey(String(row.id));
  createdKey.value = result.api_key;
  ElMessage.warning("新 Key 只显示一次，请立即安全保存");
}
async function saveSetting(key: string, value: unknown) {
  await ElMessageBox.confirm(
    `确认把 ${key} 更新为 ${value}？`,
    "策略变更二次确认",
    { type: "warning" },
  );
  await api.updateSetting(key, value as number | boolean | string);
  ElMessage.success("设置已保存并写入审计日志");
  await load();
}
async function openLogs() {
  if (!logValue.value) return;
  const result = await api.logLink(
    `${logKey.value}=${encodeURIComponent(logValue.value)}`,
  );
  window.open(result.url, "_blank", "noopener,noreferrer");
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
    const payload = JSON.parse(await file.text()) as Record<string, unknown>;
    await ElMessageBox.confirm(
      `确认导入 ${String(payload.workflow_key ?? "未知工作流")}:${String(payload.version ?? "?")}？`,
      "导入 API 工作流包",
      { type: "warning" },
    );
    await api.importWorkflow(payload);
    ElMessage.success("工作流已导入；启用前请确认模型和节点兼容性");
    await load();
  } finally {
    input.value = "";
  }
}
onMounted(load);
</script>
<template>
  <div class="page">
    <div class="page-heading">
      <div>
        <h1>{{ title }}</h1>
        <p v-if="kind === 'workflows'">导入、验证、兼容性检查和版本灰度</p>
        <p v-else-if="kind === 'logs'">
          按 job_id、request_id、node_id 与 error_code 跳转 Grafana Loki
        </p>
        <p v-else>配置与操作均通过管理 API 保存并写入审计日志</p>
      </div>
      <button v-if="kind === 'clients'" class="primary" @click="createClient">
        新建客户</button
      ><button
        v-else-if="kind === 'workflows'"
        class="primary"
        @click="workflowFile?.click()"
      >
        导入工作流包</button
      ><button
        v-else-if="kind === 'alerts'"
        class="secondary"
        @click="testFeishu"
      >
        测试飞书
      </button>
      <input
        v-if="kind === 'workflows'"
        ref="workflowFile"
        type="file"
        accept="application/json,.json"
        hidden
        @change="importWorkflow"
      />
    </div>
    <section class="ruled-section blue-rail resource">
      <div v-if="loading">正在加载…</div>
      <div v-else-if="error" class="error-banner">
        {{ error }} <button @click="load">重试</button>
      </div>
      <template v-else-if="kind === 'logs'"
        ><div class="log-builder">
          <select v-model="logKey">
            <option>job_id</option>
            <option>request_id</option>
            <option>node_id</option>
            <option>error_code</option></select
          ><input v-model="logValue" placeholder="输入精确检索值" /><button
            class="primary"
            @click="openLogs"
          >
            在 Grafana 查看
          </button>
        </div></template
      ><template v-else-if="kind === 'scheduling' || kind === 'settings'"
        ><div class="setting-row" v-for="(value, key) in settings" :key="key">
          <label>{{ key }}</label
          ><select v-if="typeof value === 'boolean'" v-model="settings[key]">
            <option :value="false">关闭</option>
            <option :value="true">开启</option></select
          ><input
            v-else-if="typeof value === 'number'"
            type="number"
            v-model.number="settings[key]"
          /><input
            v-else
            type="text"
            v-model="settings[key]"
            placeholder="例如 22:00-06:00"
          />
          <button @click="saveSetting(String(key), settings[key])">保存</button>
        </div></template
      ><template v-else
        ><div v-if="createdKey" class="one-time-key">
          <strong>新 API Key（仅显示一次）</strong><code>{{ createdKey }}</code
          ><button @click="createdKey = ''">我已保存</button>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th
                  v-for="keyName in Object.keys(rows[0] ?? {}).slice(0, 7)"
                  :key="keyName"
                >
                  {{ keyName }}
                </th>
                <th v-if="kind === 'workflows' || kind === 'clients'">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in rows" :key="String(row.id ?? index)">
                <td
                  v-for="keyName in Object.keys(rows[0] ?? {}).slice(0, 7)"
                  :key="keyName"
                >
                  <code v-if="typeof row[keyName] === 'object'">{{
                    JSON.stringify(row[keyName])
                  }}</code
                  ><span v-else>{{ row[keyName] }}</span>
                </td>
                <td v-if="kind === 'workflows'">
                  <button @click="toggle(row)">
                    {{ row.enabled ? "停用" : "启用" }}
                  </button>
                </td>
                <td v-else-if="kind === 'clients'">
                  <button @click="key(row)">生成 Key</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="!rows.length" class="empty-state">
          <strong>尚无数据</strong
          ><span>完成对应配置后，数据会显示在这里。</span>
        </div></template
      >
    </section>
  </div>
</template>
