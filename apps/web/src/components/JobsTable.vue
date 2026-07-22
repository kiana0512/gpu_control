<script setup lang="ts">
import type { JobInfo } from "../types";
import StatusMark from "./StatusMark.vue";
defineProps<{ jobs: JobInfo[] }>();
</script>
<template>
  <section class="ruled-section blue-rail">
    <div class="section-title">
      <h2>最近任务</h2>
      <router-link to="/jobs">查看全部 →</router-link>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>任务 ID</th>
            <th>工作流</th>
            <th>状态</th>
            <th>节点</th>
            <th>进度</th>
            <th>等待时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="job in jobs.slice(0, 6)" :key="job.job_id">
            <td>
              <router-link :to="`/jobs?job=${job.job_id}`">{{
                job.job_id.slice(0, 13)
              }}</router-link>
            </td>
            <td>{{ job.workflow_key }} v{{ job.workflow_version }}</td>
            <td><StatusMark :value="job.status" /></td>
            <td>{{ job.node_id ?? "—" }}</td>
            <td>
              <div class="progress">
                <i :style="{ width: `${job.progress}%` }"></i>
              </div>
              <small>{{ job.progress.toFixed(0) }}%</small>
            </td>
            <td>{{ new Date(job.created_at).toLocaleTimeString() }}</td>
            <td><button class="row-action">›</button></td>
          </tr>
          <tr v-if="!jobs.length">
            <td colspan="7" class="empty">暂无任务</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
