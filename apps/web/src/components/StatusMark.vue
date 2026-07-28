<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{ value: string }>();
const labels: Record<string, string> = {
  ACTIVE: "运行中",
  OVERFLOW: "溢出池",
  RESERVED: "已保留",
  DRAINING: "排空中",
  DISABLED: "已禁用",
  QUEUED: "排队中",
  RUNNING: "运行中",
  VALIDATING: "校验中",
  ASSEMBLING: "汇总中",
  FAILING: "失败收尾中",
  CANCELLING: "取消中",
  SUCCEEDED: "已成功",
  FAILED: "失败",
  CANCELLED: "已取消",
  TIMED_OUT: "已超时",
  ONLINE: "在线",
  OFFLINE: "离线",
  DEGRADED: "异常",
};
const label = computed(() => labels[props.value] ?? props.value);
</script>
<template>
  <span class="status-mark" :class="value.toLowerCase()" :title="value"
    ><i></i>{{ label }}</span
  >
</template>
