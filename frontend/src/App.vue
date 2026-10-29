<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { AgentAnalysis, AgentCitation, AgentStatus, AgentTurn, InventorySnapshot, MapFeature, MatchDetail, MatchEvent, MatchRow, Player, Position, Ward } from './types'
import { DIRE_BASE_FEATURES, isBaseStructure, MAP_TOWER_FEATURES, RADIANT_BASE_FEATURES } from './radiantBase'
import { HERO_SPRITE_NAMES } from './heroSprite'

type UpgradeTimelineEvent = NonNullable<NonNullable<InventorySnapshot['progression']>['upgrade_timeline']>[number]

const matches = ref<MatchRow[]>([])
const detail = ref<MatchDetail | null>(null)
const selectedId = ref('')
const matchSearch = ref('')
const eventSearch = ref('')
const time = ref(0)
const playing = ref(false)
const loading = ref(true)
const error = ref('')
const activeEvent = ref<MatchEvent | null>(null)
const selectedSlot = ref<number | null>(null)
const inventory = ref<InventorySnapshot | null>(null)
const inventoryLoading = ref(false)
const inventoryError = ref('')
const roleDraft = ref<number | null>(null)
const roleSaving = ref(false)
const roleMessage = ref('')
const agentStatus = ref<AgentStatus | null>(null)
const agentTurns = ref<AgentTurn[]>([])
const agentText = ref('')
const agentSending = ref(false)
const agentError = ref('')
const agentThreadId = ref('')
const agentInput = ref<HTMLTextAreaElement | null>(null)
const agentConversation = ref<HTMLDivElement | null>(null)
const agentSide = ref<'radiant' | 'dire'>('radiant')
const layers = ref({ heroes: true, wards: true, trails: false, objectives: true, vision: false, wardHeat: false })
type DrawingPoint = { x: number; y: number }
type MapDrawing = { id: string; color: string; points: DrawingPoint[] }
const DRAWING_COLORS = ['#f0cf83', '#79c4ce', '#d98b78', '#e8e9df']
const drawingEnabled = ref(false)
const drawingColor = ref(DRAWING_COLORS[0])
const mapDrawings = ref<MapDrawing[]>([])
const activeDrawing = ref<MapDrawing | null>(null)
const mapSvg = ref<SVGSVGElement | null>(null)
const MAP_WORLD = Object.freeze({ xmin: 7563, xmax: 25900, ymin: 7800, ymax: 25600 })
const MAP_ART = Object.freeze({ left: 59.57, right: 939.45, top: 59.57, bottom: 935.55 })
const MAP_VIEW_BOX = `${MAP_ART.left} ${MAP_ART.top} ${MAP_ART.right - MAP_ART.left} ${MAP_ART.bottom - MAP_ART.top}`
let timer = 0
let detailRequest = 0

const filteredMatches = computed(() => {
  const query = matchSearch.value.trim().toLocaleLowerCase()
  if (!query) return matches.value
  return matches.value.filter((match) =>
    `${match.radiant_name ?? ''} ${match.dire_name ?? ''} ${match.match_id ?? ''}`.toLocaleLowerCase().includes(query),
  )
})
const filteredEvents = computed(() => {
  const rows = detail.value?.events ?? []
  const query = eventSearch.value.trim().toLocaleLowerCase()
  return query ? rows.filter((event) => `${event.type} ${event.subtype ?? ''} ${event.description ?? ''}`.toLocaleLowerCase().includes(query)) : rows
})
const duration = computed(() => Math.max(0, Number(detail.value?.match.duration_s ?? 0)))
const minTime = computed(() => {
  const times = Object.values(detail.value?.positions ?? {}).flat().map((point) => Number(point.time_s))
  return Math.min(0, ...times.filter((value) => Number.isFinite(value)))
})
const currentPlayers = computed(() => detail.value?.players ?? [])
const selectedPlayer = computed(() => currentPlayers.value.find((player) => player.slot === selectedSlot.value) ?? null)
const itemGroups = computed(() => {
  const items = inventory.value?.items ?? {}
  return [
    { title: '装备栏', slots: Array.from({ length: 6 }, (_, index) => String(index)) },
    { title: '背包', slots: ['6', '7', '8'] },
    { title: '储物栏', slots: Array.from({ length: 6 }, (_, index) => String(index + 9)) },
    { title: '传送卷轴', slots: ['15'] },
    { title: '中立道具', slots: ['16'] },
  ].map((group) => ({ ...group, items: group.slots.map((slot) => ({ slot, name: items[slot] })).filter((item) => item.name) }))
})
const currentAbilities = computed(() => Object.entries(inventory.value?.progression?.abilities ?? {})
  .filter(([, level]) => Number(level) > 0)
  .sort(([a], [b]) => a.localeCompare(b)))
const currentTalents = computed(() => currentAbilities.value.filter(([name]) => name.includes('special_bonus')))
const currentSkills = computed(() => currentAbilities.value.filter(([name]) => !name.includes('special_bonus')))
const currentNeutralFinds = computed(() => (inventory.value?.progression?.neutral_finds ?? [])
  .filter((find) => find.time_s == null || Number(find.time_s) <= time.value))
const fullUpgradeTimeline = computed(() => inventory.value?.progression?.upgrade_timeline ?? [])
const currentUpgradeTimeline = computed(() => fullUpgradeTimeline.value
  .filter((event) => event.event === 'possible_consumption_window' || event.event === 'tormentor_reward_window'
    ? Number(event.end_s ?? Infinity) <= time.value
    : event.time_s == null ? time.value >= duration.value : Number(event.time_s) <= time.value))
const upgradePurchase = (kind: UpgradeTimelineEvent['kind']) => [...currentUpgradeTimeline.value]
  .reverse().find((event) => event.kind === kind && event.event === 'purchase')
const scepterConsumption = computed(() => currentUpgradeTimeline.value
  .find((event) => event.kind === 'aghanims_scepter' && event.event === 'consumption_confirmed'))
const tormentorShardReward = computed(() => currentUpgradeTimeline.value
  .find((event) => event.kind === 'aghanims_shard'
    && (event.event === 'tormentor_reward' || event.event === 'tormentor_reward_window')))
const upgradeEventLabel = (event: UpgradeTimelineEvent) => {
  const item = event.kind === 'aghanims_scepter' ? 'A 杖' : '魔晶'
  if (event.event === 'orb_selection_confirmed') {
    const orb = event.selection === 'quas' ? '冰（Quas）' : event.selection === 'wex' ? '雷（Wex）' : event.selection === 'exort' ? '火（Exort）' : '未知'
    return `${item}强化选择：${orb} · ${formatTime(event.time_s)}（元素等级快照）`
  }
  if (event.event === 'consumption_confirmed') return `A 杖吞食已确认 · ${event.time_s == null ? '时间未知' : formatTime(event.time_s)}（阿哈利姆福佑购买日志）`
  if (event.event === 'tormentor_reward') return `魔方奖励魔晶 · ${formatTime(event.time_s)}（终局状态与唯一魔方击杀记录吻合）`
  if (event.event === 'tormentor_reward_window') return `魔方奖励魔晶 · ${formatTime(event.start_s)}–${formatTime(event.end_s)}（多次击杀，时间范围推定）`
  if (event.event === 'purchase') return `${item}购买记录 · ${event.time_s == null ? '时间未知' : formatTime(event.time_s)}（精确日志）`
  if (event.event === 'inventory_first_seen') return `${item}首次出现在装备快照 · ${event.time_s == null ? '时间未知' : formatTime(event.time_s)}（采样观测）`
  return `${item}可能吞食区间 · ${formatTime(event.start_s)}–${formatTime(event.end_s)}（推断，非精确时间）`
}
const hasItem = (needles: string[]) => Object.values(inventory.value?.items ?? {})
  .some((item) => needles.some((needle) => item.toLowerCase().includes(needle)))
const terminalFlag = (key: string) => inventory.value?.progression?.endgame?.[key] === 1
const hasTerminalInventory = computed(() => inventory.value?.snapshot_time_s != null
  && inventory.value.snapshot_time_s >= duration.value)
const aghsStatus = computed(() => {
  if (scepterConsumption.value) return `本场已吞食 · ${formatTime(scepterConsumption.value.time_s)}（福佑购买日志确认）`
  if (hasItem(['ultimate_scepter'])) return '当前物品栏持有'
  const purchase = upgradePurchase('aghanims_scepter')
  if (purchase) {
    const timeLabel = purchase.time_s == null ? '时间未知' : formatTime(purchase.time_s)
    const consumed = hasTerminalInventory.value ? terminalFlag('aghanims_scepter') ? '终局已吞食' : '终局未吞食' : '吞食状态未知'
    return `本场已购买 · ${timeLabel}（精确日志）；${consumed}`
  }
  return !hasTerminalInventory.value ? '当前未持有；吞食状态未知'
    : terminalFlag('aghanims_scepter') ? '本场已吞食' : '本场未吞食'
})
const shardStatus = computed(() => {
  if (hasItem(['aghanims_shard'])) return '当前物品栏持有'
  if (tormentorShardReward.value?.event === 'tormentor_reward') return `魔方奖励已确认 · ${formatTime(tormentorShardReward.value.time_s)}`
  if (tormentorShardReward.value?.event === 'tormentor_reward_window') return `魔方奖励已确认 · ${formatTime(tormentorShardReward.value.start_s)}–${formatTime(tormentorShardReward.value.end_s)}（时间范围）`
  const purchase = upgradePurchase('aghanims_shard')
  if (purchase) return `本场已获得 · ${purchase.time_s == null ? '时间未知' : formatTime(purchase.time_s)}（购买日志）`
  return !hasTerminalInventory.value ? '当前未持有；获得状态未知'
    : terminalFlag('aghanims_shard') ? '本场已获得' : '本场未获得'
})
const invokerUpgradeChoice = (kind: UpgradeTimelineEvent['kind']) => {
  const selected = [...currentUpgradeTimeline.value].reverse()
    .find((event) => event.kind === kind && event.event === 'orb_selection_confirmed')
  if (selected) {
    const orb = selected.selection === 'quas' ? '冰（Quas）' : selected.selection === 'wex' ? '雷（Wex）' : selected.selection === 'exort' ? '火（Exort）' : '未知'
    return `${orb} · ${formatTime(selected.time_s)}`
  }
  const futureSelection = fullUpgradeTimeline.value.some((event) => event.kind === kind
    && event.event === 'orb_selection_confirmed' && event.time_s != null && Number(event.time_s) > time.value)
  if (futureSelection) return '当前时点尚未选择'
  const hasAcquisition = currentUpgradeTimeline.value.some((event) => event.kind === kind
    && ['purchase', 'consumption_confirmed', 'inventory_first_seen', 'tormentor_reward', 'tormentor_reward_window'].includes(event.event))
  const itemNeedle = kind === 'aghanims_scepter' ? 'ultimate_scepter' : 'aghanims_shard'
  const hasCurrentItem = Object.values(inventory.value?.items ?? {}).some((item) => item.toLowerCase().includes(itemNeedle))
  if (hasAcquisition || hasCurrentItem || (hasTerminalInventory.value && terminalFlag(kind))) {
    return '未知（录像未记录明确选项）'
  }
  return hasTerminalInventory.value ? '本场未获得' : '当前尚未获得'
}
const currentHeroes = computed(() => currentPlayers.value.flatMap((player) => {
  const position = positionAt(detail.value?.positions[String(player.slot)] ?? [], time.value)
  return position ? [{ player, position }] : []
}))
const currentWards = computed(() => (detail.value?.wards ?? []).filter((ward) =>
  Number(ward.time_s) <= time.value && (ward.end_s == null || Number(ward.end_s) > time.value),
))
const currentPhase = computed(() => time.value < 0 || Math.floor(time.value / 300) % 2 === 1 ? '夜晚' : '白天')
const timeLabel = computed(() => formatTime(time.value))
const durationLabel = computed(() => formatTime(duration.value))
const currentObservationCount = computed(() => (detail.value?.map_observations ?? []).filter((item) => item.time_s != null && item.time_s <= time.value).length)
const visibleMapFeatures = computed(() => [
  ...(detail.value?.map_features ?? []).filter((feature) => feature.kind !== 'tower' && !isBaseStructure(feature)),
  ...MAP_TOWER_FEATURES,
  ...RADIANT_BASE_FEATURES.filter((feature) => feature.kind !== 'tower'),
  ...DIRE_BASE_FEATURES.filter((feature) => feature.kind !== 'tower'),
].filter((feature) => feature.kind !== 'tower' || towerStatus(feature).visible))
const latestWardAnalysis = computed(() => [...agentTurns.value].reverse()
  .find((turn) => turn.analysis?.kind === 'ward_hotspots')?.analysis ?? null)
const wardHeatspots = computed(() => latestWardAnalysis.value?.hotspots ?? [])

function formatTime(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const seconds = Math.trunc(Number(value))
  const sign = seconds < 0 ? '−' : ''
  const absolute = Math.abs(seconds)
  return `${sign}${String(Math.floor(absolute / 60)).padStart(2, '0')}:${String(absolute % 60).padStart(2, '0')}`
}

function positionAt(rows: Position[], second: number): Position | null {
  let latest: Position | null = null
  for (const point of rows) {
    if (Number(point.time_s) <= second && (!latest || Number(point.time_s) > Number(latest.time_s))) latest = point
  }
  return latest && second - Number(latest.time_s) <= 4 ? latest : null
}

function mapPoint(x: number, y: number): { x: number; y: number; clipped: boolean } {
  const clipped = x < MAP_WORLD.xmin || x > MAP_WORLD.xmax || y < MAP_WORLD.ymin || y > MAP_WORLD.ymax
  const cx = Math.max(MAP_WORLD.xmin, Math.min(MAP_WORLD.xmax, x))
  const cy = Math.max(MAP_WORLD.ymin, Math.min(MAP_WORLD.ymax, y))
  return {
    x: MAP_ART.left + (MAP_ART.right - MAP_ART.left) * (cx - MAP_WORLD.xmin) / (MAP_WORLD.xmax - MAP_WORLD.xmin),
    y: MAP_ART.top + (MAP_ART.bottom - MAP_ART.top) * (MAP_WORLD.ymax - cy) / (MAP_WORLD.ymax - MAP_WORLD.ymin),
    clipped,
  }
}

function itemLabel(name: string): string {
  return name.replace(/^item_/, '').replaceAll('_', ' ')
}

function itemIcon(name: string): string {
  const slug = name.toLowerCase().replace(/^item_/, '')
  return /^[a-z0-9_]+$/.test(slug)
    ? `https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/${slug}.png`
    : ''
}

function linePoints(points: DrawingPoint[]): string {
  return points.map((point) => `${point.x},${point.y}`).join(' ')
}

function loadMapDrawings(matchId: string): MapDrawing[] {
  if (!matchId) return []
  try {
    const stored = JSON.parse(localStorage.getItem(`dota-review-map-lines:${matchId}`) || '[]')
    if (!Array.isArray(stored)) return []
    return stored.slice(-80).flatMap((line): MapDrawing[] => {
      if (!line || typeof line.id !== 'string' || !DRAWING_COLORS.includes(line.color) || !Array.isArray(line.points)) return []
      const points = line.points.slice(0, 600).flatMap((point: unknown): DrawingPoint[] => {
        if (!point || typeof point !== 'object') return []
        const { x, y } = point as DrawingPoint
        return Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x <= 1000 && y >= 0 && y <= 1000
          ? [{ x, y }]
          : []
      })
      return points.length > 1 ? [{ id: line.id, color: line.color, points }] : []
    })
  } catch {
    return []
  }
}

function persistMapDrawings() {
  if (!selectedId.value) return
  try {
    localStorage.setItem(`dota-review-map-lines:${selectedId.value}`, JSON.stringify(mapDrawings.value.slice(-80)))
  } catch {
    // Keep drawing usable if browser storage is full or unavailable.
  }
}

function drawingPoint(event: PointerEvent): DrawingPoint | null {
  const svg = mapSvg.value
  const matrix = svg?.getScreenCTM()
  if (!svg || !matrix) return null
  const point = svg.createSVGPoint()
  point.x = event.clientX
  point.y = event.clientY
  const local = point.matrixTransform(matrix.inverse())
  return { x: Math.max(0, Math.min(1000, local.x)), y: Math.max(0, Math.min(1000, local.y)) }
}

function finishCurrentDrawing() {
  if (!activeDrawing.value) return
  if (activeDrawing.value.points.length < 2) {
    mapDrawings.value = mapDrawings.value.filter((line) => line.id !== activeDrawing.value?.id)
  }
  activeDrawing.value = null
  persistMapDrawings()
}

function startMapDrawing(event: PointerEvent) {
  if (!drawingEnabled.value || event.button !== 0 || !detail.value) return
  const point = drawingPoint(event)
  if (!point) return
  event.preventDefault()
  const line: MapDrawing = { id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, color: drawingColor.value, points: [point] }
  mapDrawings.value = [...mapDrawings.value, line]
  activeDrawing.value = line
  mapSvg.value?.setPointerCapture(event.pointerId)
}

function continueMapDrawing(event: PointerEvent) {
  const line = activeDrawing.value
  if (!line) return
  const point = drawingPoint(event)
  if (!point) return
  const previous = line.points[line.points.length - 1]
  if (Math.hypot(point.x - previous.x, point.y - previous.y) >= 2.5) line.points.push(point)
}

function stopMapDrawing() {
  finishCurrentDrawing()
}

function toggleDrawing() {
  drawingEnabled.value = !drawingEnabled.value
  if (!drawingEnabled.value) finishCurrentDrawing()
}

function undoMapDrawing() {
  if (activeDrawing.value) finishCurrentDrawing()
  mapDrawings.value = mapDrawings.value.slice(0, -1)
  persistMapDrawings()
}

function clearMapDrawings() {
  activeDrawing.value = null
  mapDrawings.value = []
  persistMapDrawings()
}

function handleDrawingKey(event: KeyboardEvent) {
  if (event.key === 'Escape' && drawingEnabled.value) {
    finishCurrentDrawing()
    drawingEnabled.value = false
  }
}

function featurePoint(feature: MapFeature): { x: number; y: number; clipped: boolean } {
  const point = mapPoint(feature.x, feature.y)
  // Replay entity anchors for these neutral bosses sit off-center from their pits.
  const pitOffset = feature.kind === 'roshan' ? { x: 40, y: -8 }
    : feature.kind === 'tormentor' ? { x: 26, y: -18 } : { x: 0, y: 0 }
  return { ...point, x: point.x + pitOffset.x, y: point.y + pitOffset.y }
}

function featureIcon(feature: MapFeature): string | null {
  const name = (feature.name || '').toLowerCase()
  const icons: Record<string, string> = {
    ancient: 'ancient-minimap.png', lotus_pool: 'lotus-pool-minimap.png',
    roshan: 'roshan-minimap.png', tormentor: 'tormentor-minimap.png',
    twin_gate: 'twin-gate-minimap.png', watch_tower: 'watcher-minimap.png',
  }
  if (feature.kind === 'tower') return `/assets/icons/${name.includes('_mid') ? 'tower-45' : 'tower-90'}-minimap.png`
  if (feature.kind === 'barracks') return `/assets/icons/${name.includes('_mid') ? 'barracks-45' : 'barracks-90'}-minimap.png`
  if (feature.kind === 'neutral_camp') {
    const campSize = name.includes('ancient') ? 'ancient' : name.includes('large') ? 'large'
      : name.includes('medium') ? 'medium' : 'small'
    return `/assets/icons/neutral-camp-${campSize}-minimap.png`
  }
  if (feature.kind === 'shop') {
    const nearBase = Math.min(Math.hypot(feature.x - 8928, feature.y - 9446),
      Math.hypot(feature.x - 23792, feature.y - 23232)) < 4000
    return `/assets/icons/${nearBase ? 'main-shop' : 'secret-shop'}-minimap.png`
  }
  return icons[feature.kind] ? `/assets/icons/${icons[feature.kind]}` : null
}

function featureIconSize(feature: MapFeature): number {
  const sizes: Record<string, number> = {
    tower: 17, barracks: 17, ancient: 26, roshan: 28, tormentor: 27,
    twin_gate: 22, lotus_pool: 19, neutral_camp: 13, watch_tower: 16, shop: 15,
  }
  return sizes[feature.kind] ?? 16
}

function playerName(player: Player): string {
  return player.player_name || heroLabel(player) || `位置 ${player.slot}`
}

function heroLabel(player: Player): string {
  return (player.hero || '').replace(/^npc_dota_hero_/, '').replaceAll('_', ' ')
}

function heroPortrait(player: Player): string {
  const name = heroLabel(player).toLowerCase().replaceAll(' ', '_')
  return /^[a-z0-9_]+$/.test(name)
    ? `https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/${name}.png`
    : ''
}

function heroSpriteClass(player: Player): string {
  const name = (player.hero || '').replace(/^npc_dota_hero_/, '').toLowerCase()
  return HERO_SPRITE_NAMES.has(name) ? `npc_dota_hero_${name}` : ''
}

function removeFailedPortrait(event: Event) {
  ;(event.target as HTMLImageElement).remove()
}

function featureLabel(feature: MapFeature): string {
  const names: Record<string, string> = {
    tower: '塔', barracks: '兵营', ancient: '遗迹', roshan: '肉山', tormentor: '魔方',
    twin_gate: '传送门', lotus_pool: '莲花池', neutral_camp: '野区', shop: '商店', watch_tower: '侦察塔',
  }
  return names[feature.kind] ?? '地图目标'
}

function towerIdentity(name: string): string {
  return name.toLowerCase().replace(/(npc_dota_(?:goodguys|badguys)_tower4)(?:_(?:top|bot))?$/, '$1')
}

function towerLabel(feature: MapFeature): string {
  const match = /npc_dota_(?:goodguys|badguys)_tower([1-4])(?:_(top|mid|bot))?$/i.exec(feature.name || '')
  const lane = match?.[2] === 'top' ? '上路' : match?.[2] === 'mid' ? '中路' : match?.[2] === 'bot' ? '下路' : '基地'
  const team = feature.team === 2 ? '天辉' : feature.team === 3 ? '夜魇' : '未知队伍'
  return `${team}${lane}${match?.[1] ?? ''}塔`
}

function towerStatus(feature: MapFeature): { state: 'alive' | 'destroyed' | 'partial' | 'unknown'; visible: boolean; label: string } {
  const name = feature.name?.toLowerCase()
  if (!name) return { state: 'unknown', visible: true, label: '摧毁状态未知' }

  const identity = towerIdentity(name)
  const deaths = (detail.value?.events ?? []).filter((event) => event.type === 'tower'
    && event.subtype != null && towerIdentity(event.subtype) === identity
    && Number.isFinite(Number(event.time_s)) && Number(event.time_s) <= time.value)
  const isTierFourPair = /npc_dota_(?:goodguys|badguys)_tower4$/.test(identity)
  if (isTierFourPair) {
    const destroyedCount = Math.min(2, deaths.length)
    if (destroyedCount === 2) return { state: 'destroyed', visible: false, label: '两座均已摧毁' }
    if (destroyedCount === 1) return { state: 'partial', visible: true, label: '已摧毁 1/2 座，具体位置无法从录像确定' }
  } else if (deaths.length) {
    return { state: 'destroyed', visible: false, label: '已摧毁' }
  }

  if (detail.value?.match.parse_status === 'complete') return { state: 'alive', visible: true, label: '存活' }
  return { state: 'unknown', visible: true, label: '摧毁状态未知（录像数据不完整）' }
}

function featureTitle(feature: MapFeature): string {
  if (feature.kind === 'tower') {
    const status = towerStatus(feature)
    return `${towerLabel(feature)} · ${status.label} · 7.41 地图基准位置`
  }
  return `${feature.name || featureLabel(feature)} · 录像或地图目录位置`
}

function teamClass(team: number | null | undefined): string {
  return team === 2 ? 'radiant' : team === 3 ? 'dire' : 'neutral'
}

function eventLabel(event: MatchEvent): string {
  return event.description || event.subtype || event.type
}

async function loadMatches() {
  loading.value = true
  error.value = ''
  try {
    const response = await fetch('/api/matches')
    if (!response.ok) throw new Error('比赛列表接口暂不可用')
    const data = await response.json() as { matches: MatchRow[] }
    matches.value = data.matches
    if (data.matches.length && !selectedId.value) await selectMatch(data.matches[0].id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '无法读取比赛数据'
  } finally {
    loading.value = false
  }
}

async function selectMatch(id: string, keepAgentContext = false) {
  const requestId = ++detailRequest
  selectedId.value = id
  mapDrawings.value = loadMapDrawings(id)
  activeDrawing.value = null
  drawingEnabled.value = false
  playing.value = false
  activeEvent.value = null
  selectedSlot.value = null
  inventory.value = null
  roleMessage.value = ''
  if (!keepAgentContext) {
    agentTurns.value = []
    agentThreadId.value = ''
    agentError.value = ''
  }
  layers.value.wardHeat = false
  error.value = ''
  try {
    const response = await fetch(`/api/matches/${encodeURIComponent(id)}`)
    if (!response.ok) throw new Error(response.status === 404 ? '找不到这场比赛' : '比赛详情接口暂不可用')
    const nextDetail = await response.json() as MatchDetail
    if (requestId !== detailRequest) return
    detail.value = nextDetail
    time.value = Math.min(0, duration.value)
  } catch (err) {
    if (requestId !== detailRequest) return
    detail.value = null
    error.value = err instanceof Error ? err.message : '无法读取比赛详情'
  }
}

async function loadAgentStatus() {
  try {
    const response = await fetch('/api/agent/status')
    if (response.ok) agentStatus.value = await response.json() as AgentStatus
  } catch {
    agentStatus.value = null
  }
}

async function sendAgentMessage(mode?: 'player' | 'ward_hotspots' | 'team_style') {
  if (agentSending.value || !selectedId.value) return
  const message = mode === 'player'
    ? '分析选中选手的经济、补刀和眼位参与情况。'
    : mode === 'ward_hotspots'
      ? '分析这场比赛的眼位热区，指出双方重复布眼的位置。'
      : mode === 'team_style'
        ? `分析${agentSide.value === 'radiant' ? '天辉' : '夜魇'}方已导入比赛中的战队风格。`
        : agentText.value.trim()
  if (!message) return
  const pendingTurn = { role: 'user' as const, text: message }
  agentTurns.value.push(pendingTurn)
  agentText.value = ''
  agentSending.value = true
  agentError.value = ''
  try {
    const response = await fetch('/api/agent/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ match_id: selectedId.value, message, slot: selectedSlot.value,
        side: agentSide.value, mode: mode ?? null, thread_id: agentThreadId.value || null }),
    })
    const result = await response.json() as {
      thread_id: string; answer: string; analysis: AgentAnalysis; citations: AgentCitation[]; intent: string; notice?: string
    }
    if (!response.ok) throw new Error((result as unknown as { detail?: string }).detail || '分析请求失败')
    agentThreadId.value = result.thread_id
    agentTurns.value.push({ role: 'assistant', text: `${result.notice ? `${result.notice}\n` : ''}${result.answer}`,
      analysis: result.analysis, citations: result.citations })
    if (result.intent === 'ward_hotspots') layers.value.wardHeat = true
    await nextTick()
    if (agentConversation.value) agentConversation.value.scrollTop = agentConversation.value.scrollHeight
  } catch (err) {
    const failedTurn = agentTurns.value.indexOf(pendingTurn)
    if (failedTurn !== -1) agentTurns.value.splice(failedTurn, 1)
    if (!mode && !agentText.value) agentText.value = message
    agentError.value = err instanceof Error ? err.message : '暂时无法分析，请稍后重试。'
  } finally {
    agentSending.value = false
  }
}

function handleAgentKey(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void sendAgentMessage()
  }
}

async function jumpToCitation(citation: AgentCitation) {
  if (citation.match_id !== selectedId.value) await selectMatch(citation.match_id, true)
  if (citation.time_s !== null) jumpTo(citation.time_s)
}

let inventoryTimer = 0
let inventoryRequest = 0
watch([selectedSlot, time, selectedId], ([slot, second, id]) => {
  window.clearTimeout(inventoryTimer)
  inventory.value = null
  inventoryError.value = ''
  if (slot === null || !id) return
  inventoryTimer = window.setTimeout(async () => {
    const requestId = ++inventoryRequest
    inventoryLoading.value = true
    try {
      const response = await fetch(`/api/matches/${encodeURIComponent(id)}/inventory?slot=${slot}&time_s=${Math.trunc(second)}`)
      if (!response.ok) throw new Error('装备数据暂不可用')
      const result = await response.json() as InventorySnapshot
      if (requestId === inventoryRequest) inventory.value = result
    } catch (err) {
      if (requestId === inventoryRequest) inventoryError.value = err instanceof Error ? err.message : '无法读取装备数据'
    } finally {
      if (requestId === inventoryRequest) inventoryLoading.value = false
    }
  }, 220)
}, { immediate: false })

watch(selectedPlayer, (player) => {
  roleDraft.value = player?.role ?? null
  roleMessage.value = ''
})

async function saveRole() {
  if (!selectedPlayer.value || !roleDraft.value) return
  roleSaving.value = true
  roleMessage.value = ''
  try {
    const response = await fetch(`/api/matches/${encodeURIComponent(selectedId.value)}/players/${selectedPlayer.value.slot}/role`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role: roleDraft.value }),
    })
    if (!response.ok) throw new Error('保存失败，请检查数据库权限后重试')
    const result = await response.json() as { role: number; role_source: string }
    selectedPlayer.value.role = result.role
    selectedPlayer.value.role_source = result.role_source
    roleMessage.value = '分路已保存'
  } catch (err) {
    roleMessage.value = err instanceof Error ? err.message : '保存失败'
  } finally {
    roleSaving.value = false
  }
}

function jumpTo(second: number, event?: MatchEvent) {
  time.value = Math.max(minTime.value, Math.min(duration.value, second))
  activeEvent.value = event ?? null
}

function togglePlayback() {
  playing.value = !playing.value
}

watch(playing, (active) => {
  window.clearInterval(timer)
  if (active) {
    timer = window.setInterval(() => {
      if (time.value >= duration.value) {
        playing.value = false
        time.value = duration.value
      } else time.value = Math.min(duration.value, time.value + 1)
    }, 1000)
  }
})

onMounted(() => {
  window.addEventListener('keydown', handleDrawingKey)
  void loadMatches()
  void loadAgentStatus()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleDrawingKey)
  window.clearInterval(timer)
  window.clearTimeout(inventoryTimer)
})
</script>

<template>
  <div class="app-frame">
    <header class="topbar">
      <a class="brand" href="/" aria-label="Dota 2 录像复盘首页">
        <img class="brand-mark" src="/assets/dota2-game-icon.jpg" alt="" width="34" height="34" />
        <span class="brand-copy"><strong>复盘工作台</strong><small>DOTA 2 MATCH REVIEW</small></span>
      </a>
      <div class="top-context">
        <span class="live-dot"></span>
        <span>本地资料库</span>
        <span class="context-separator"></span>
        <span>{{ matches.length }} 场比赛</span>
      </div>
      <button class="quiet-button" type="button" @click="agentInput?.focus()">智能复盘 Agent</button>
    </header>

    <main class="workspace">
      <aside class="library panel">
        <div class="panel-heading">
          <div><span class="eyebrow">比赛资料库</span><h1>最近比赛</h1></div>
          <span class="count-badge">{{ matches.length }}</span>
        </div>
        <label class="search-box">
          <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m13 13 4 4"/></svg>
          <input v-model="matchSearch" type="search" placeholder="搜索对阵或比赛 ID" aria-label="搜索比赛" />
        </label>
        <div class="library-label">所有比赛 <span>{{ filteredMatches.length }}</span></div>
        <div class="match-list" aria-live="polite">
          <button v-for="match in filteredMatches" :key="match.id" class="match-row" :class="{ selected: selectedId === match.id }" type="button" @click="selectMatch(match.id)">
            <span class="match-pair"><span>{{ match.radiant_name || '天辉' }}</span><i>vs</i><span>{{ match.dire_name || '夜魇' }}</span></span>
            <span class="match-meta"><span>{{ match.match_id ? `#${match.match_id}` : '比赛 ID 未知' }}</span><span>{{ formatTime(match.duration_s) }}</span></span>
            <span class="match-foot"><span>{{ match.patch || '版本未知' }}</span><span :class="match.parse_status === 'complete' ? 'status-ok' : 'status-warn'">{{ match.parse_status === 'complete' ? '解析完成' : '部分数据' }}</span></span>
          </button>
          <p v-if="!loading && !filteredMatches.length && !error" class="empty-copy">没有匹配的比赛。</p>
          <p v-if="loading" class="empty-copy">正在读取比赛资料…</p>
        </div>
        <div class="library-footer"><span class="footer-mark">◈</span><span>数据来自本地复盘库</span></div>
      </aside>

      <section class="review panel">
        <div v-if="detail" class="match-header">
          <div class="match-title"><span class="eyebrow">比赛复盘</span><h2>{{ detail.match.radiant_name || '天辉' }} <small>对阵</small> {{ detail.match.dire_name || '夜魇' }}</h2></div>
          <div class="header-tags"><span>{{ detail.match.match_id ? `#${detail.match.match_id}` : '比赛 ID 未知' }}</span><span>{{ detail.match.patch || '版本未知' }}</span></div>
        </div>
        <div v-else class="match-header"><div class="match-title"><span class="eyebrow">比赛复盘</span><h2>{{ error ? '数据连接中断' : '选择一场比赛' }}</h2></div></div>

        <div v-if="error" class="error-strip" role="alert"><span>{{ error }}</span><button type="button" @click="loadMatches">重试</button></div>

        <div class="map-toolbar">
          <div class="map-heading"><span class="map-glyph">⌖</span><div><strong>战术地图</strong><small>7.41 示意底图 · 位置按录像坐标绘制</small></div></div>
          <div class="layer-controls" aria-label="地图图层">
            <button type="button" :aria-pressed="layers.heroes" @click="layers.heroes = !layers.heroes"><img class="layer-icon" src="/assets/icons/hero-ally-minimap.png" alt="" />英雄</button>
            <button type="button" :aria-pressed="layers.wards" @click="layers.wards = !layers.wards"><img class="layer-icon" src="/assets/icons/observer-ward-minimap.png" alt="" />眼位</button>
            <button type="button" :aria-pressed="layers.trails" @click="layers.trails = !layers.trails"><i class="legend-line"></i>轨迹</button>
            <button type="button" :aria-pressed="layers.objectives" @click="layers.objectives = !layers.objectives"><img class="layer-icon" src="/assets/icons/tower-90-minimap.png" alt="" />目标</button>
            <button type="button" :aria-pressed="layers.vision" @click="layers.vision = !layers.vision"><i class="legend-ring"></i>视距估算</button>
            <button v-if="wardHeatspots.length" type="button" :aria-pressed="layers.wardHeat" @click="layers.wardHeat = !layers.wardHeat"><i class="heat-swatch"></i>眼位热区</button>
            <button type="button" class="draw-toggle" :disabled="!detail" :aria-pressed="drawingEnabled" @click="toggleDrawing"><i class="legend-line"></i>{{ drawingEnabled ? '退出画线' : '画线' }}</button>
            <template v-if="drawingEnabled">
              <span class="drawing-colors" aria-label="线条颜色">
                <button v-for="color in DRAWING_COLORS" :key="color" type="button" :aria-label="`选择${color}线条`" :aria-pressed="drawingColor === color" :title="`线条颜色 ${color}`" @click="drawingColor = color"><i :style="{ backgroundColor: color }"></i></button>
              </span>
              <button type="button" :disabled="!mapDrawings.length" @click="undoMapDrawing">撤销</button>
              <button type="button" :disabled="!mapDrawings.length" @click="clearMapDrawings">清空</button>
            </template>
          </div>
        </div>

        <div class="map-stage">
          <div class="map-stamp"><span>{{ currentPhase }}</span><b>{{ timeLabel }}</b></div>
          <svg ref="mapSvg" class="tactical-map" :class="{ 'drawing-active': drawingEnabled }" :viewBox="MAP_VIEW_BOX" role="img" aria-label="Dota 2 示意地图和当前时刻的录像实体" @pointerdown="startMapDrawing" @pointermove="continueMapDrawing" @pointerup="stopMapDrawing" @pointercancel="stopMapDrawing" @lostpointercapture="stopMapDrawing">
            <image :href="'/assets/dota2_map_7_41.png'" x="0" y="0" width="1000" height="1000" preserveAspectRatio="none" />
            <rect v-if="currentPhase === '夜晚'" :x="MAP_ART.left" :y="MAP_ART.top" :width="MAP_ART.right - MAP_ART.left" :height="MAP_ART.bottom - MAP_ART.top" fill="#101a32" opacity=".3" pointer-events="none" />
            <g v-if="layers.vision && layers.heroes" class="vision-layer">
              <ellipse v-for="entry in currentHeroes" :key="`vision-${entry.player.slot}`" :cx="mapPoint(entry.position.x, entry.position.y).x" :cy="mapPoint(entry.position.x, entry.position.y).y" :rx="((MAP_ART.right - MAP_ART.left) * (currentPhase === '白天' ? 1800 : 800)) / (MAP_WORLD.xmax - MAP_WORLD.xmin)" :ry="((MAP_ART.bottom - MAP_ART.top) * (currentPhase === '白天' ? 1800 : 800)) / (MAP_WORLD.ymax - MAP_WORLD.ymin)" :class="teamClass(entry.player.team)" />
            </g>
            <g v-if="layers.trails" class="trail-layer">
              <polyline v-for="player in currentPlayers.filter(p => selectedSlot === null || selectedSlot === p.slot)" :key="`trail-${player.slot}`" :points="(detail?.positions[String(player.slot)] ?? []).filter(p => p.time_s <= time && time - p.time_s <= 90).map(p => { const xy = mapPoint(p.x, p.y); return `${xy.x},${xy.y}` }).join(' ')" :class="teamClass(player.team)" />
            </g>
            <g v-if="layers.wardHeat && wardHeatspots.length" class="ward-heat-layer">
              <g v-for="(spot, index) in wardHeatspots" :key="`heat-${index}`" :transform="`translate(${mapPoint(spot.x, spot.y).x} ${mapPoint(spot.x, spot.y).y})`" :class="teamClass(spot.team)">
                <title>{{ spot.team === 2 ? '天辉' : spot.team === 3 ? '夜魇' : '未知队伍' }}眼位热区 · {{ spot.placements }} 次 · {{ spot.kind === 'observer' ? '侦查守卫' : '岗哨守卫' }}</title>
                <circle class="heat-glow" :r="10 + Math.min(18, spot.placements * 3)" />
                <circle class="heat-core" :r="3 + Math.min(5, spot.placements)" />
              </g>
            </g>
            <g v-if="layers.objectives" class="feature-layer">
              <g v-for="feature in visibleMapFeatures" :key="feature.feature_id" :data-feature-id="feature.feature_id" :data-kind="feature.kind" :data-state="feature.kind === 'tower' ? towerStatus(feature).state : undefined" :data-world-x="feature.x" :data-world-y="feature.y" :transform="`translate(${featurePoint(feature).x} ${featurePoint(feature).y})`" :class="['feature-marker', feature.kind, teamClass(feature.team), feature.kind === 'tower' ? `tower-${towerStatus(feature).state}` : '']">
                <title>{{ featureTitle(feature) }}</title>
                <circle :r="featureIconSize(feature) / 2 + 3" />
                <image v-if="featureIcon(feature)" :href="featureIcon(feature)!" :x="-featureIconSize(feature) / 2" :y="-featureIconSize(feature) / 2" :width="featureIconSize(feature)" :height="featureIconSize(feature)" preserveAspectRatio="xMidYMid meet" />
                <text v-else y="3.5">{{ featureLabel(feature).slice(0, 1) }}</text>
              </g>
            </g>
            <g v-if="layers.wards" class="ward-layer">
              <g v-for="(ward, index) in currentWards.filter(w => w.x !== null && w.y !== null)" :key="`ward-${index}`" :data-time="ward.time_s" :data-world-x="ward.x" :data-world-y="ward.y" :transform="`translate(${mapPoint(ward.x!, ward.y!).x} ${mapPoint(ward.x!, ward.y!).y})`" :class="['ward-marker', ward.kind === 'sentry' ? 'sentry' : 'observer']">
                <title>{{ ward.kind === 'sentry' ? '岗哨守卫' : '侦查守卫' }} · {{ formatTime(ward.time_s) }}{{ ward.end_s == null ? ' · 结束时间未知' : ` 至 ${formatTime(ward.end_s)}` }}</title>
                <image :href="ward.kind === 'sentry' ? '/assets/icons/sentry-ward-minimap.png' : '/assets/icons/observer-ward-minimap.png'" x="-8" y="-8" width="16" height="16" preserveAspectRatio="xMidYMid meet" />
              </g>
            </g>
            <g v-if="layers.heroes" class="hero-layer">
              <g v-for="entry in currentHeroes" :key="`hero-${entry.player.slot}`" :data-slot="entry.player.slot" :data-world-x="entry.position.x" :data-world-y="entry.position.y" :transform="`translate(${mapPoint(entry.position.x, entry.position.y).x} ${mapPoint(entry.position.x, entry.position.y).y})`" :class="['hero-marker', teamClass(entry.player.team)]">
                <title>{{ playerName(entry.player) }} · {{ heroLabel(entry.player) || '英雄未知' }} · {{ formatTime(entry.position.time_s) }}</title>
                <rect class="hero-halo" x="-17" y="-17" width="34" height="34" rx="2" />
                <foreignObject v-if="heroSpriteClass(entry.player)" class="hero-sprite-foreign" x="-16" y="-16" width="32" height="32">
                  <span xmlns="http://www.w3.org/1999/xhtml" class="d2mh" :class="heroSpriteClass(entry.player)"></span>
                </foreignObject>
                <image v-else :href="heroPortrait(entry.player)" x="-16" y="-16" width="32" height="32" preserveAspectRatio="xMidYMid slice" clip-path="inset(0 round 2px)" @error="removeFailedPortrait" />
              </g>
            </g>
            <g class="user-drawing-layer" pointer-events="none">
              <polyline v-for="line in mapDrawings" :key="line.id" :points="linePoints(line.points)" :stroke="line.color" />
              <polyline v-if="activeDrawing" class="drawing-preview" :points="linePoints(activeDrawing.points)" :stroke="activeDrawing.color" />
            </g>
          </svg>
          <div v-if="!detail" class="map-empty"><span>⌖</span><p>地图将在选中比赛后显示</p></div>
          <div v-if="drawingEnabled" class="map-drawing-hint">拖动地图绘制 · Esc 结束</div>
          <div class="map-corner">按 7.41 世界坐标投影</div>
          <div class="map-key"><span><i class="key-radiant" />天辉英雄</span><span><i class="key-dire" />夜魇英雄</span><span><img src="/assets/icons/observer-ward-minimap.png" alt="" />眼位</span></div>
        </div>

        <div class="roster-strip" v-if="detail">
          <div class="team-roster radiant-roster"><span class="team-name">天辉</span><button v-for="player in currentPlayers.filter(p => p.team === 2)" :key="player.slot" type="button" :class="{ picked: selectedSlot === player.slot }" :title="`${playerName(player)} · ${heroLabel(player) || '英雄未知'}`" :aria-pressed="selectedSlot === player.slot" @click="selectedSlot = player.slot; layers.trails = true"><b><span>{{ (heroLabel(player) || playerName(player)).slice(0, 1) }}</span><img v-if="heroPortrait(player)" :src="heroPortrait(player)" alt="" class="hero-portrait" @error="removeFailedPortrait" /></b><span>{{ heroLabel(player) || playerName(player) }}</span></button></div>
          <div class="team-roster dire-roster"><span class="team-name">夜魇</span><button v-for="player in currentPlayers.filter(p => p.team === 3)" :key="player.slot" type="button" :class="{ picked: selectedSlot === player.slot }" :title="`${playerName(player)} · ${heroLabel(player) || '英雄未知'}`" :aria-pressed="selectedSlot === player.slot" @click="selectedSlot = player.slot; layers.trails = true"><b><span>{{ (heroLabel(player) || playerName(player)).slice(0, 1) }}</span><img v-if="heroPortrait(player)" :src="heroPortrait(player)" alt="" class="hero-portrait" @error="removeFailedPortrait" /></b><span>{{ heroLabel(player) || playerName(player) }}</span></button></div>
        </div>

        <section v-if="selectedPlayer" class="inventory-panel" aria-label="当前英雄道具和装备">
          <div class="inventory-title"><div><span class="eyebrow">{{ heroLabel(selectedPlayer) || '英雄' }} · {{ playerName(selectedPlayer) }}</span><strong>当前装备</strong></div><span>{{ timeLabel }}</span></div>
          <div class="inventory-tools"><label>分路<select v-model.number="roleDraft" aria-label="编辑英雄分路"><option :value="null" disabled>未设置</option><option v-for="role in [1,2,3,4,5]" :key="role" :value="role">{{ role }} 号位</option></select></label><button type="button" :disabled="roleSaving || roleDraft === selectedPlayer.role || roleDraft === null" @click="saveRole">{{ roleSaving ? '保存中…' : '保存分路' }}</button><small aria-live="polite">{{ roleMessage }}</small></div>
          <p v-if="inventoryLoading" class="inventory-empty">正在读取 {{ timeLabel }} 的物品栏…</p>
          <p v-else-if="inventoryError" class="inventory-empty">{{ inventoryError }}</p>
          <p v-else-if="inventory?.coverage === 'unavailable'" class="inventory-empty">该时间点暂无装备快照；这场录像可能尚未完成背包数据补录。</p>
          <div v-else-if="inventory" class="hero-build">
            <div class="item-groups">
              <section v-for="group in itemGroups" :key="group.title">
                <h3>{{ group.title }}</h3>
                <div class="item-slots">
                  <div v-for="slot in group.slots" :key="slot" class="item-slot" :class="{ occupied: inventory.items[slot] }" :title="inventory.items[slot] || `${group.title}空位`">
                    <template v-if="inventory.items[slot]"><img :src="itemIcon(inventory.items[slot]!)" :alt="itemLabel(inventory.items[slot]!)" class="item-icon" loading="lazy" @error="removeFailedPortrait" /><span class="item-name">{{ itemLabel(inventory.items[slot]!) }}</span></template>
                    <span v-else>—</span>
                  </div>
                </div>
              </section>
              <section class="snapshot-upgrades" aria-label="当前装备快照中的神杖和魔晶状态">
                <h3>神杖与魔晶 · 快照状态</h3>
                <span :class="{ acquired: hasItem(['ultimate_scepter']) || (hasTerminalInventory && terminalFlag('aghanims_scepter')) }">A 杖 {{ aghsStatus }}</span>
                <span :class="{ acquired: hasItem(['aghanims_shard']) || (hasTerminalInventory && terminalFlag('aghanims_shard')) }">魔晶 {{ shardStatus }}</span>
                <div v-if="selectedPlayer.hero === 'npc_dota_hero_invoker'" class="snapshot-invoker-choice">
                  <span>卡尔神杖元素 · {{ invokerUpgradeChoice('aghanims_scepter') }}</span>
                  <span>卡尔魔晶元素 · {{ invokerUpgradeChoice('aghanims_shard') }}</span>
                  <small>冰对应 Quas，雷对应 Wex，火对应 Exort；快照证据不足时标为未知。</small>
                </div>
                <small v-if="!hasTerminalInventory">非终局快照只显示当前库存和已确认事件。</small>
                <small v-else-if="terminalFlag('aghanims_scepter') || terminalFlag('aghanims_shard')">已显示录像记录的终局状态。</small>
                <div v-if="currentUpgradeTimeline.length" class="upgrade-timeline"><span v-for="(event, index) in currentUpgradeTimeline" :key="`${event.kind}-${event.event}-${index}`">{{ upgradeEventLabel(event) }}</span></div>
                <small v-else>暂无可用的购买日志或库存变化证据。</small>
                <small class="snapshot-time">快照 {{ formatTime(inventory.snapshot_time_s) }}</small>
              </section>
            </div>
            <div class="build-details">
              <section><h3>技能等级</h3><div class="ability-list"><span v-for="([name, level]) in currentSkills" :key="name">{{ itemLabel(name) }} · {{ level }}</span><span v-if="!currentSkills.length" class="build-empty">该录像没有技能等级数据</span></div></section>
              <section><h3>天赋加点</h3><div class="ability-list"><span v-for="([name, level]) in currentTalents" :key="name">{{ itemLabel(name) }} · {{ level }}</span><span v-if="!currentTalents.length" class="build-empty">当前尚无可识别的天赋选择</span></div></section>
              <section v-if="currentNeutralFinds.length"><h3>中立道具获取记录</h3><div class="ability-list"><span v-for="(find, index) in currentNeutralFinds" :key="`${find.tick}-${index}`">{{ find.item ? itemLabel(find.item) : '中立道具' }}<template v-if="find.tier"> · T{{ find.tier }}</template><template v-if="find.time_s != null"> · {{ formatTime(find.time_s) }}</template></span></div></section>
            </div>
          </div>
        </section>

        <section class="timeline-panel" aria-label="比赛时间轴">
          <div class="timeline-top"><div><span class="eyebrow">录像时间</span><strong>{{ timeLabel }}</strong><small>/ {{ durationLabel }}</small></div><div class="timeline-actions"><button type="button" aria-label="后退 5 秒" @click="jumpTo(time - 5)">−5</button><button class="play-button" type="button" :disabled="!detail" :aria-label="playing ? '暂停' : '播放'" @click="togglePlayback">{{ playing ? 'Ⅱ' : '▶' }}</button><button type="button" aria-label="前进 5 秒" @click="jumpTo(time + 5)">+5</button></div><span class="time-phase"><i :class="currentPhase === '白天' ? 'sun' : 'moon'"></i>{{ currentPhase }}</span></div>
          <input v-model.number="time" class="time-range" type="range" :min="minTime" :max="Math.max(duration, minTime + 1)" step="1" :disabled="!detail" aria-label="选择游戏时间" />
          <div class="time-scale"><span>{{ formatTime(minTime) }}</span><span>10:00</span><span>20:00</span><span>30:00</span><span>{{ durationLabel }}</span></div>
          <div class="timeline-events" aria-label="时间轴事件">
            <button v-for="(event, index) in detail?.events ?? []" :key="`${event.time_s}-${index}`" type="button" :style="{ left: `${Math.max(0, Math.min(100, ((event.time_s - minTime) / Math.max(1, duration - minTime)) * 100))}%` }" :title="`${formatTime(event.time_s)} ${eventLabel(event)}`" :class="['event-tick', { focused: activeEvent === event }]" @click="jumpTo(event.time_s, event)"></button>
          </div>
        </section>
      </section>

      <aside class="inspector panel">
        <section class="inspector-section current-section">
          <div class="inspector-heading"><div><span class="eyebrow">录像状态</span><h2>当前时刻</h2></div><span class="clock-chip">{{ timeLabel }}</span></div>
          <div class="metric-grid">
            <div><span>英雄位置</span><strong>{{ currentHeroes.length }}<small> / 10</small></strong></div>
            <div><span>有效眼位</span><strong>{{ currentWards.length }}</strong></div>
            <div><span>地图观察</span><strong>{{ currentObservationCount }}</strong></div>
            <div><span>解析状态</span><strong class="metric-status">{{ detail?.match.parse_status === 'complete' ? '完整' : detail ? '部分' : '—' }}</strong></div>
          </div>
          <div class="evidence-note"><span class="note-mark">i</span><p>英雄位置超过 4 秒未更新时隐藏。结束时间未知的眼位会显示到录像时间轴末尾。</p></div>
        </section>

        <section class="inspector-section event-section">
          <div class="inspector-heading"><div><span class="eyebrow">比赛进程</span><h2>事件记录</h2></div><span class="event-total">{{ detail?.events.length ?? 0 }} 条</span></div>
          <label class="event-search"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m13 13 4 4"/></svg><input v-model="eventSearch" type="search" placeholder="筛选事件" aria-label="筛选比赛事件" /></label>
          <div class="events-scroll">
            <button v-for="(event, index) in filteredEvents" :key="`${event.time_s}-${index}`" type="button" :class="['event-row', { selected: activeEvent === event }]" @click="jumpTo(event.time_s, event)">
              <time>{{ formatTime(event.time_s) }}</time><span class="event-content"><b>{{ eventLabel(event) }}</b><small>{{ event.subtype || event.type }}<template v-if="event.team"> · {{ event.team === 2 ? '天辉' : '夜魇' }}</template></small></span><span class="event-spark" :class="teamClass(event.team)"></span>
            </button>
            <p v-if="detail && filteredEvents.length === 0" class="empty-copy">没有符合条件的事件。</p>
            <p v-if="!detail" class="empty-copy">选择比赛后查看事件。</p>
          </div>
        </section>

        <section class="inspector-section agent-section" aria-label="智能复盘 Agent">
          <div class="inspector-heading agent-heading"><div><span class="eyebrow">LANGGRAPH · 证据驱动</span><h2>智能复盘 Agent</h2></div><span class="agent-state" :class="agentStatus?.mode === 'llm' ? 'model-ready' : ''">{{ agentStatus?.mode === 'llm' ? '模型已接入' : '证据分析模式' }}</span></div>
          <p class="agent-caption">{{ agentStatus?.message || '连接复盘数据中…' }}</p>
          <div class="agent-shortcuts">
            <button type="button" :disabled="agentSending" @click="sendAgentMessage('player')">分析选手</button>
            <button type="button" :disabled="agentSending" @click="sendAgentMessage('ward_hotspots')">眼位热区</button>
            <button type="button" :disabled="agentSending" @click="sendAgentMessage('team_style')">战队风格</button>
          </div>
          <label class="agent-side-picker">分析队伍
            <select v-model="agentSide" aria-label="选择分析队伍">
              <option value="radiant">{{ detail?.match.radiant_name || '天辉' }}</option>
              <option value="dire">{{ detail?.match.dire_name || '夜魇' }}</option>
            </select>
          </label>
          <div ref="agentConversation" class="agent-conversation" aria-live="polite">
            <article v-for="(turn, index) in agentTurns" :key="`${index}-${turn.role}`" :class="['agent-turn', turn.role]">
              <span class="agent-turn-role">{{ turn.role === 'user' ? '你' : '复盘助手' }}</span>
              <p>{{ turn.text }}</p>
              <div v-if="turn.analysis?.kind === 'player'" class="analysis-cards">
                <div v-for="row in turn.analysis.checkpoints" :key="row.target_time_s" class="analysis-card">
                  <b>{{ Math.floor(row.target_time_s / 60) }} 分钟</b><span>经济 {{ Number(row.net_worth).toLocaleString() }}</span><span>补刀 {{ row.last_hits }}</span><span>队内经济 {{ row.team_net_worth_rank }}/{{ row.team_size }}</span>
                </div>
                <small>眼位：真眼 {{ turn.analysis.observer_wards }} · 假眼 {{ turn.analysis.sentry_wards }}</small>
              </div>
              <div v-else-if="turn.analysis?.kind === 'ward_hotspots'" class="analysis-cards">
                <small>坐标热区（眼位记录 {{ turn.analysis.placement_count }} 个）</small>
                <button v-for="(spot, spotIndex) in turn.analysis.hotspots?.slice(0, 4)" :key="spotIndex" type="button" class="analysis-card hotspot-card" @click="jumpTo(spot.first_time_s)">
                  <b>{{ spot.team === 2 ? '天辉' : '夜魇' }} · {{ spot.placements }} 次</b><span>坐标 {{ spot.x }}, {{ spot.y }}</span><span>{{ spot.kind === 'observer' ? '侦查守卫' : '岗哨守卫' }}</span>
                </button>
              </div>
              <div v-else-if="turn.analysis?.kind === 'team_style'" class="analysis-cards team-analysis">
                <small>{{ turn.analysis.team }} · {{ turn.analysis.metrics?.matches }} 场样本 · {{ turn.analysis.metrics?.sample_quality }}</small>
                <div class="team-style-metrics"><span>胜率 {{ turn.analysis.metrics?.win_rate == null ? '—' : `${Math.round(turn.analysis.metrics.win_rate * 100)}%` }}</span><span>前 15 分钟击杀 {{ turn.analysis.metrics?.average_early_kills_0_15m }}</span><span>烟雾 {{ turn.analysis.metrics?.average_smokes }} / 场</span><span>眼位 {{ turn.analysis.metrics?.average_wards }} / 场</span><span>拆塔 {{ turn.analysis.metrics?.average_towers_taken }} / 场</span></div>
              </div>
              <div v-if="turn.citations?.length" class="agent-citations">
                <button v-for="(citation, citationIndex) in turn.citations.slice(0, 6)" :key="citationIndex" type="button" @click="jumpToCitation(citation)">{{ citation.time_s === null ? '来源' : formatTime(citation.time_s) }} · {{ citation.label }}</button>
              </div>
            </article>
            <p v-if="!agentTurns.length" class="agent-empty">选中英雄后可看个人成长曲线，也能询问眼位和战队打法。</p>
            <p v-if="agentSending" class="agent-working"><i></i>正在查询比赛证据并整理分析…</p>
          </div>
          <p v-if="agentError" class="agent-error" role="alert">{{ agentError }}</p>
          <div class="agent-composer"><textarea ref="agentInput" v-model="agentText" rows="2" maxlength="4000" placeholder="问这场比赛……" aria-label="向复盘 Agent 提问" @keydown="handleAgentKey"></textarea><button type="button" :disabled="agentSending || !agentText.trim()" aria-label="发送问题" @click="sendAgentMessage()">{{ agentSending ? '…' : '↑' }}</button></div>
          <small class="agent-footnote">数据结论来自录像记录；样本和缺失项会在回答中标注。</small>
        </section>

        <section class="inspector-section selected-event" v-if="activeEvent">
          <span class="eyebrow">已定位事件</span><strong>{{ eventLabel(activeEvent) }}</strong><span>{{ formatTime(activeEvent.time_s) }} · {{ activeEvent.subtype || activeEvent.type }}</span>
        </section>
        <section class="inspector-section data-limits">
          <span class="eyebrow">数据说明</span>
          <p>地图为 7.41 风格示意图，坐标未经地形校准。昼夜按游戏时间推算；视距圈仅为标准范围估算，不代表真实战争迷雾。</p>
          <p v-if="detail?.match.patch?.includes('7.41')">子版本可能按发布时间窗口推定。</p>
        </section>
      </aside>
    </main>

    <footer class="bottom-bar"><span><i class="connection-light"></i>{{ error ? '数据库连接异常' : 'MySQL 资料库已连接' }}</span><span>复盘工作台 · 本地交互与分路编辑</span></footer>
  </div>
</template>
