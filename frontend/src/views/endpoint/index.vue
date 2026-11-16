<template>
  <section class="page" data-module="endpoint">
    <header class="page-head">
      <div>
        <h2>接口管理管理</h2>
        <p class="page-desc">维护API端点的输入、执行约束与结果记录，为接口管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记API端点</button>
        <button class="btn" type="button" @click="exportRows">导出接口管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload(1)">
      <label class="filter-item">
        <span>端点编号</span>
        <input v-model="filters.keyword" placeholder="按端点编号检索" />
      </label>
      <label class="filter-item">
        <span>接口地址</span>
        <input v-model="filters.address" placeholder="按接口地址检索/过滤" />
      </label>
      <label class="filter-item">
        <span>接入状态</span>
        <select v-model="filters.onboardStatus">
          <option value="">全部</option>
          <option v-for="s in onboardStatuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="tab-row">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </div>

    <table v-if="activeTab === 'main'" class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>接入状态</th>
          <th>失败原因</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <button v-if="column === '端点编号'" class="link" type="button" @click="openDetail(row)">
              {{ row[column] ?? '—' }}
            </button>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td>
            <span class="badge" :class="badgeClass(row)">{{ row['接入状态'] ?? row.status }}</span>
          </td>
          <td class="fail-cell">{{ row['失败原因'] || '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无接口管理数据，可先登记API端点</td>
        </tr>
      </tbody>
    </table>

    <table v-else-if="activeTab === 'quarantine'" class="data-table">
      <thead>
        <tr>
          <th>端点编号</th>
          <th>接口地址</th>
          <th>认证模板</th>
          <th>请求头配置（隔离可回看）</th>
          <th>导入批次</th>
          <th>失败原因</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in quarantineRows" :key="String(row.id)">
          <td>{{ row['端点编号'] ?? '—' }}</td>
          <td>{{ row['接口地址'] ?? '—' }}</td>
          <td>{{ row['认证模板'] || '空值' }}</td>
          <td class="header-cell">{{ row['请求头配置'] || '—' }}</td>
          <td>{{ row['导入批次'] ?? '—' }}</td>
          <td class="fail-cell">{{ row['失败原因'] ?? '—' }}</td>
        </tr>
        <tr v-if="!quarantineRows.length">
          <td colspan="6" class="empty-state">暂无被隔离的接入登记</td>
        </tr>
      </tbody>
    </table>

    <table v-else class="data-table">
      <thead>
        <tr>
          <th>端点编号</th>
          <th>接口地址</th>
          <th>认证模板</th>
          <th>接入状态</th>
          <th>回执说明 / 失败原因</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in receiptRows" :key="String(row.id)">
          <td>{{ row['端点编号'] ?? '—' }}</td>
          <td>{{ row['接口地址'] ?? '—' }}</td>
          <td>{{ row['认证模板'] || '空值' }}</td>
          <td>
            <span class="badge" :class="row['接入状态'] === '已接入' ? 'badge-ok' : 'badge-reject'">
              {{ row['接入状态'] }}
            </span>
          </td>
          <td class="fail-cell">{{ row['回执说明'] || row['失败原因'] || '—' }}</td>
        </tr>
        <tr v-if="!receiptRows.length">
          <td colspan="5" class="empty-state">暂无接入回执</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条记录<template v-if="activeTab === 'main'"> · 第 {{ page }} / {{ totalPages }} 页</template></span>
      <span v-if="activeTab === 'main'" class="pager">
        <button class="btn" type="button" :disabled="page <= 1" @click="reload(page - 1)">上一页</button>
        <button class="btn" type="button" :disabled="page >= totalPages" @click="reload(page + 1)">下一页</button>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记/编辑弹窗：认证方式、请求头、参数模板与接入状态整包提交 -->
    <div v-if="formVisible" class="modal-mask" @click.self="closeForm">
      <div class="modal">
        <h3>{{ form.id ? '重新提交端点接入（同编号只更新一条待处理记录）' : '登记API端点接入' }}</h3>
        <div class="form-grid">
          <label v-for="field in formFields" :key="field.name" class="form-item" :class="{ required: field.required }">
            <span>{{ field.label }}</span>
            <textarea
              v-if="field.multiline"
              v-model="form.values[field.name]"
              :placeholder="field.placeholder"
              rows="3"
            ></textarea>
            <select v-else-if="field.options" v-model="form.values[field.name]">
              <option v-for="opt in field.options" :key="opt" :value="opt">{{ opt }}</option>
            </select>
            <input v-else v-model="form.values[field.name]" :placeholder="field.placeholder" />
          </label>
        </div>
        <p v-if="formError" class="error-text">{{ formError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeForm">取消</button>
          <button class="btn primary" type="button" :disabled="saving" @click="submitForm">
            {{ saving ? '保存中…' : '整包保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 详情弹窗：认证参数与原配置完整回看 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal">
        <h3>端点详情 · {{ detail['端点编号'] }}</h3>
        <dl class="detail-list">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ detail[field] ?? '—' }}</dd>
          </template>
          <dt>接入状态</dt>
          <dd>{{ detail['接入状态'] ?? detail.status }}</dd>
          <dt>导入批次</dt>
          <dd>{{ detail['导入批次'] ?? '—' }}</dd>
          <dt>失败原因</dt>
          <dd class="fail-cell">{{ detail['失败原因'] || '—' }}</dd>
        </dl>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="editFromDetail">用此配置重新提交</button>
          <button class="btn ghost" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/endpoint'
const PAGE_SIZE = 10
const columns = ['端点编号', '接口地址', '请求方法', '认证方式', '请求头配置', '参数模板', '响应格式', '超时秒数']
const actions = ['提交校验', '激活端点', '停用端点']
const onboardStatuses = ['待接入', '已接入', '已驳回']
const tabs = [
  { key: 'main', label: '接口管理清单（待处理区）' },
  { key: 'quarantine', label: '隔离区（被拒绝项）' },
  { key: 'receipt', label: '接入回执' },
] as const

type TabKey = (typeof tabs)[number]['key']

const rows = ref<Row[]>([])
const quarantineRows = ref<Row[]>([])
const receiptRows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const errorMessage = ref('')
const activeTab = ref<TabKey>('main')
const filters = reactive({ keyword: '', address: '', onboardStatus: '' })

const stats = ref([
  { label: 'API端点总数', value: 0 },
  { label: '已接入端点', value: 0 },
  { label: '待接入端点', value: 0 },
  { label: '已驳回/隔离', value: 0 },
])

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

const formFields = [
  { name: '端点编号', label: '端点编号', required: true, placeholder: '如 CFG-A17' },
  { name: '接口地址', label: '接口地址', required: true, placeholder: '如 /v2/orders' },
  { name: '请求方法', label: '请求方法', required: true, options: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'] },
  { name: '认证方式', label: '认证方式', placeholder: '如 API Key / Bearer Token；无认证可留空（可能被判冲突隔离）' },
  { name: '认证模板', label: '认证模板', placeholder: '如 Header-7，无认证留空' },
  { name: '请求头配置', label: '请求头配置', multiline: true, placeholder: '如 X-API-Key: {{key}}' },
  { name: '参数模板', label: '参数模板', multiline: true, placeholder: '如 {"order":"{{id}}"}' },
  { name: '响应格式', label: '响应格式', placeholder: 'application/json' },
  { name: '超时秒数', label: '超时秒数', placeholder: '如 15' },
  { name: '导入批次', label: '导入批次', options: ['修正批次', '导入批次'] },
]

const detailFields = [...columns, '规范化地址']

const formVisible = ref(false)
const saving = ref(false)
const formError = ref('')
const form = reactive<{ id: number | null; values: Record<string, string> }>({
  id: null,
  values: {},
})
const detail = ref<Row | null>(null)

function emptyFormValues(): Record<string, string> {
  const values: Record<string, string> = {}
  for (const field of formFields) values[field.name] = ''
  values['导入批次'] = '修正批次'
  return values
}

function badgeClass(row: Row): string {
  const status = String(row['接入状态'] ?? '')
  if (status === '已接入') return 'badge-ok'
  if (status === '已驳回' || row['失败原因']) return 'badge-reject'
  return 'badge-pending'
}

function switchTab(key: TabKey) {
  activeTab.value = key
  if (key === 'quarantine') void loadQuarantine()
  else if (key === 'receipt') void loadReceipts()
  else void reload(1)
}

function resetFilters() {
  filters.keyword = ''
  filters.address = ''
  filters.onboardStatus = ''
  void reload(1)
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  form.id = null
  form.values = emptyFormValues()
  formError.value = ''
  formVisible.value = true
}

function openDetail(row: Row) {
  detail.value = row
}

function editFromDetail() {
  if (!detail.value) return
  form.id = Number(detail.value.id) || null
  form.values = emptyFormValues()
  for (const field of formFields) {
    const value = detail.value[field.name]
    if (value !== null && value !== undefined) form.values[field.name] = String(value)
  }
  detail.value = null
  formError.value = ''
  formVisible.value = true
}

function closeForm() {
  formVisible.value = false
  formError.value = ''
}

async function submitForm() {
  formError.value = ''
  saving.value = true
  try {
    const response = await request(`${ENDPOINT}/registration`, {
      method: 'POST',
      body: JSON.stringify({ values: form.values }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      // 任一字段未落库都会返回 ok=false，这里直接展示按接口地址回写的失败原因。
      throw new Error(payload.message || '接入登记未成功，配置未整包落库')
    }
    formVisible.value = false
    activeTab.value = 'main'
    await reload(1)
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '接入登记保存失败'
  } finally {
    saving.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '接口管理动作未生效，请稍后重试')
    }
    await reload(page.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接口管理操作失败'
  }
}

function buildQuery(targetPage: number): string {
  const params = new URLSearchParams()
  if (filters.keyword.trim()) params.set('keyword', filters.keyword.trim())
  if (filters.address.trim()) params.set('address', filters.address.trim())
  if (filters.onboardStatus) params.set('onboardStatus', filters.onboardStatus)
  params.set('page', String(targetPage))
  params.set('size', String(PAGE_SIZE))
  return params.toString()
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats/summary`)
    if (!response.ok) return
    const summary = await response.json()
    stats.value[0].value = summary.total ?? 0
    stats.value[1].value = summary.accepted ?? 0
    stats.value[2].value = summary.pending ?? 0
    stats.value[3].value = (summary.rejected ?? 0) + (summary.quarantined ?? 0)
  } catch {
    // 统计卡片读取失败不阻塞列表渲染。
  }
}

async function reload(targetPage = 1) {
  errorMessage.value = ''
  page.value = targetPage
  try {
    const response = await request(`${ENDPOINT}?${buildQuery(targetPage)}`)
    if (!response.ok) throw new Error('API端点列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    void loadStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接口管理列表读取失败'
  }
}

async function loadQuarantine() {
  try {
    const suffix = filters.address.trim() ? `?address=${encodeURIComponent(filters.address.trim())}` : ''
    const response = await request(`${ENDPOINT}/quarantine${suffix}`)
    if (!response.ok) throw new Error('隔离区读取失败')
    const payload = await response.json()
    quarantineRows.value = payload.items ?? []
    total.value = payload.total ?? quarantineRows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '隔离区读取失败'
  }
}

async function loadReceipts() {
  try {
    const suffix = filters.keyword.trim() ? `?code=${encodeURIComponent(filters.keyword.trim())}` : ''
    const response = await request(`${ENDPOINT}/receipts${suffix}`)
    if (!response.ok) throw new Error('接入回执读取失败')
    const payload = await response.json()
    receiptRows.value = payload.items ?? []
    total.value = payload.total ?? receiptRows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接入回执读取失败'
  }
}

onMounted(() => void reload(1))
</script>

<style scoped>
.tab-row { display: flex; gap: 8px; margin-bottom: 12px; }
.tab-btn { border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 13px; }
.tab-btn.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.badge-ok { background: #dcfae6; color: #067647; }
.badge-pending { background: #fef0c7; color: #b54708; }
.badge-reject { background: #fee4e2; color: #b42318; }
.fail-cell { color: #b42318; max-width: 260px; word-break: break-all; }
.header-cell { max-width: 240px; word-break: break-all; }
.pager { display: flex; gap: 6px; }
.pager .btn:disabled { opacity: 0.5; cursor: not-allowed; }
.modal-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45); display: flex; align-items: center; justify-content: center; z-index: 20; }
.modal { background: #fff; border-radius: 10px; padding: 20px 24px; width: 760px; max-width: 92vw; max-height: 88vh; overflow: auto; }
.modal h3 { margin: 0 0 14px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.form-item span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.form-item.required span::after { content: ' *'; color: #b42318; }
.form-item input, .form-item textarea, .form-item select { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 13px; font-family: inherit; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.detail-list { display: grid; grid-template-columns: 130px 1fr; gap: 6px 12px; margin: 0; font-size: 13px; }
.detail-list dt { color: var(--muted); }
.detail-list dd { margin: 0; word-break: break-all; }
</style>
