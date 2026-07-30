<script setup lang="ts">
import { computed, ref } from "vue";
import { api } from "../api";
import type { AssetProcessingOverview, Dashboard } from "../types";
import { useAutoRefresh } from "../composables/useAutoRefresh";

const dashboard = ref<Dashboard | null>(null);
const assets = ref<AssetProcessingOverview | null>(null);
const error = ref("");
const origin = window.location.origin;
const comfyUrl = `http://${window.location.hostname}:8188/#551d82b0-b1fb-483a-a5ea-564bdb813625`;
const imageclipUrl = `${origin}/api/v1/services/imageclip-rgba`;
const modelviewUrl = `${origin}/api/v1/services/modelview-inpaint`;
const roughnessUrl = `${origin}/api/v1/services/modelview-roughness`;
const uvUrl = `${origin}/api/v1/assets/uv/process`;
const retopologyUrl = `${origin}/api/v1/assets/retopology/process`;
const modelviewPromptEnabled =
  import.meta.env.VITE_MODELVIEW_PROMPT_ENABLED === "true";
const controlNode = computed(() =>
  dashboard.value?.nodes.find((node) => node.id === "control-4090"),
);

async function load() {
  error.value = "";
  try {
    const [gpuDashboard, assetOverview] = await Promise.all([
      api.dashboard(),
      api.assetProcessing(),
    ]);
    dashboard.value = gpuDashboard;
    assets.value = assetOverview;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "系统信息加载失败";
    throw cause;
  }
}

function open(url: string) {
  window.open(url, "_blank", "noopener,noreferrer");
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page system-info-page">
    <div class="page-heading">
      <div>
        <h1>系统信息</h1>
        <p>只读展示当前真实服务地址与运行状态</p>
      </div>
      <div>
        <span
          class="health-dot"
          :class="
            (assets?.summary.online_workers ?? 0) > 0 ? 'online' : 'offline'
          "
        ></span
        ><span
          ><strong>CPU Asset 平面</strong
          ><small
            >{{ assets?.summary.online_workers ?? 0 }} 个 Worker ·
            {{ assets?.summary.total_slots ?? 0 }} 个并发槽</small
          ></span
        >
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
        ><button class="secondary" @click="run">立即刷新</button>
      </div>
    </div>

    <div v-if="error" class="error-banner persistent-error">
      <strong>控制平面连接失败</strong><span>{{ error }}</span
      ><button @click="run">重试</button>
    </div>

    <section class="system-status-strip">
      <div>
        <span class="health-dot" :class="error ? 'offline' : 'online'"></span
        ><span
          ><strong>控制平面</strong
          ><small>{{ error ? "连接失败" : "运行正常" }}</small></span
        >
      </div>
      <div>
        <span
          class="health-dot"
          :class="controlNode?.health === 'ONLINE' ? 'online' : 'offline'"
        ></span
        ><span
          ><strong>4090 主控</strong
          ><small
            >{{ controlNode?.health === "ONLINE" ? "在线" : "离线" }} ·
            {{ controlNode?.mode === "ACTIVE" ? "正在接单" : "未接单" }}</small
          ></span
        >
      </div>
      <div>
        <span
          class="health-dot"
          :class="
            dashboard?.nodes.some((node) => node.health === 'ONLINE')
              ? 'online'
              : 'offline'
          "
        ></span
        ><span
          ><strong>可用 GPU</strong
          ><small
            >{{
              dashboard?.nodes.filter((node) => node.health === "ONLINE")
                .length ?? 0
            }}
            台真实在线</small
          ></span
        >
      </div>
    </section>

    <section class="system-section">
      <header>
        <h2>访问地址</h2>
        <p>以下地址来自当前浏览器访问的真实主机。</p>
      </header>
      <div class="endpoint-list">
        <div>
          <span
            ><strong>管理控制台</strong
            ><small>管理员查看任务、节点和客户</small></span
          ><code>{{ origin }}</code
          ><button class="secondary" @click="open(origin)">打开</button>
        </div>
        <div>
          <span
            ><strong>ComfyUI</strong><small>手工编辑和调试工作流</small></span
          ><code>{{ comfyUrl }}</code
          ><button class="primary" @click="open(comfyUrl)">打开</button>
        </div>
      </div>
    </section>

    <section class="system-section">
      <header>
        <h2>图片服务 API</h2>
        <p>业务软件上传图片，成功响应直接返回最终图片。</p>
      </header>
      <div class="endpoint-list">
        <div>
          <span
            ><strong>ImageClip RGBA 抠图</strong
            ><small>POST · multipart 字段 image</small></span
          ><code>{{ imageclipUrl }}</code>
        </div>
        <div>
          <span
            ><strong>ModelView 局部重绘</strong
            ><small
              >POST · image 必填 ·
              {{
                modelviewPromptEnabled
                  ? "prompt 可选"
                  : "prompt 候选协议待安全窗口启用"
              }}</small
            ></span
          ><code>{{ modelviewUrl }}</code>
        </div>
        <div>
          <span
            ><strong>PBR 粗糙度生成</strong
            ><small>POST · image 必填 · 固定生产提示词</small></span
          ><code>{{ roughnessUrl }}</code>
        </div>
      </div>
      <router-link class="inline-link" to="/clients"
        >查看完整调用方法 →</router-link
      >
    </section>

    <section class="system-section">
      <header>
        <h2>3D 资产处理 API</h2>
        <p>
          异步提交后通过 Job URL 或 SSE 获取阶段、进度和 ETA；与 GPU
          推理队列完全隔离。
        </p>
      </header>
      <div class="endpoint-list">
        <div>
          <span
            ><strong>Blender PBR UV</strong
            ><small>POST · asset + metadata · 成功原子发布 5 项</small></span
          ><code>{{ uvUrl }}</code>
        </div>
        <div>
          <span
            ><strong>AI 重拓扑</strong
            ><small>POST · project + metadata + 可选多视角参考图</small></span
          ><code>{{ retopologyUrl }}</code>
        </div>
        <div>
          <span
            ><strong>任务进度 / SSE</strong
            ><small>GET · 状态为事实，SSE 支持 Last-Event-ID 续传</small></span
          ><code>{{ origin }}/api/v1/assets/jobs/&lt;job_id&gt;</code>
        </div>
      </div>
      <router-link class="inline-link" to="/asset-processing"
        >查看 Asset Worker、任务和交付物 →</router-link
      >
    </section>
  </div>
</template>
