import type { MapFeature } from './types'

// Static Dota map anchors (game world coordinates + 16384 replay origin).
// Source mapdata: https://github.com/devilesk/dota-map-coordinates/blob/master/mapdata.json
// Radiant T3s retain the earlier fountain spacing adjustment. T4s and Ancient
// use the Dire layout reflected across the map center, nudged a little toward
// their fountain so the tower markers sit behind the stair lips and off the rax axes.
export const RADIANT_BASE_FEATURES: MapFeature[] = [
  { feature_id: 'radiant-base:tower3_top', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower3_top', x: 9887, y: 13364 },
  { feature_id: 'radiant-base:tower3_mid', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower3_mid', x: 12028, y: 12522 },
  { feature_id: 'radiant-base:tower3_bot', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower3_bot', x: 12821, y: 10364 },
  { feature_id: 'radiant-base:barracks_melee_top', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_melee_rax_top', x: 10048, y: 12626 },
  { feature_id: 'radiant-base:barracks_range_top', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_range_rax_top', x: 9540, y: 12625 },
  { feature_id: 'radiant-base:barracks_melee_mid', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_melee_rax_mid', x: 11712, y: 11832 },
  { feature_id: 'radiant-base:barracks_range_mid', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_range_rax_mid', x: 11324, y: 12185 },
  { feature_id: 'radiant-base:barracks_melee_bot', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_melee_rax_bot', x: 12104, y: 10024 },
  { feature_id: 'radiant-base:barracks_range_bot', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_range_rax_bot', x: 12105, y: 10531 },
  { feature_id: 'radiant-base:tower4_top', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower4_top', x: 10970, y: 11797 },
  { feature_id: 'radiant-base:tower4_bot', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower4_bot', x: 11282, y: 11472 },
  { feature_id: 'radiant-base:ancient', kind: 'ancient', team: 2, name: 'npc_dota_goodguys_fort', x: 10743, y: 11271 },
]

export const DIRE_BASE_FEATURES: MapFeature[] = [
  { feature_id: 'dire-base:tower4_top', kind: 'tower', team: 3, name: 'npc_dota_badguys_tower4_top', x: 21798, y: 20971 },
  { feature_id: 'dire-base:tower4_bot', kind: 'tower', team: 3, name: 'npc_dota_badguys_tower4_bot', x: 21486, y: 21296 },
  { feature_id: 'dire-base:ancient', kind: 'ancient', team: 3, name: 'npc_dota_badguys_fort', x: 22025, y: 21497 },
]

// Canonical tower anchors are included even when a replay parser cannot expose
// building entities. T3/T4 base anchors above retain the manually aligned map
// positions; these anchors come from a complete 7.41 replay map snapshot.
const OUTER_TOWER_FEATURES: MapFeature[] = [
  { feature_id: 'radiant-tower:tier1-top', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower1_top', x: 10048, y: 18240 },
  { feature_id: 'radiant-tower:tier1-mid', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower1_mid', x: 14840, y: 14976 },
  { feature_id: 'radiant-tower:tier1-bot', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower1_bot', x: 21243, y: 10004 },
  { feature_id: 'radiant-tower:tier2-top', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower2_top', x: 9883, y: 15512 },
  { feature_id: 'radiant-tower:tier2-mid', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower2_mid', x: 13193, y: 13457 },
  { feature_id: 'radiant-tower:tier2-bot', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower2_bot', x: 16024, y: 10128 },
  { feature_id: 'dire-tower:tier1-top', kind: 'tower', team: 3, name: 'npc_dota_badguys_tower1_top', x: 11109, y: 22420 },
  { feature_id: 'dire-tower:tier1-mid', kind: 'tower', team: 3, name: 'npc_dota_badguys_tower1_mid', x: 16908, y: 17036 },
  { feature_id: 'dire-tower:tier1-bot', kind: 'tower', team: 3, name: 'npc_dota_badguys_tower1_bot', x: 22653, y: 14144 },
  { feature_id: 'dire-tower:tier2-top', kind: 'tower', team: 3, name: 'npc_dota_badguys_tower2_top', x: 16256, y: 22400 },
  { feature_id: 'dire-tower:tier2-mid', kind: 'tower', team: 3, name: 'npc_dota_badguys_tower2_mid', x: 18880, y: 18496 },
  { feature_id: 'dire-tower:tier2-bot', kind: 'tower', team: 3, name: 'npc_dota_badguys_tower2_bot', x: 22784, y: 16768 },
  { feature_id: 'dire-base:tower3_top', kind: 'tower', team: 3, name: 'npc_dota_badguys_tower3_top', x: 19936, y: 22160 },
  { feature_id: 'dire-base:tower3_mid', kind: 'tower', team: 3, name: 'npc_dota_badguys_tower3_mid', x: 20656, y: 20143 },
  { feature_id: 'dire-base:tower3_bot', kind: 'tower', team: 3, name: 'npc_dota_badguys_tower3_bot', x: 22720, y: 19416 },
]

export const MAP_TOWER_FEATURES: MapFeature[] = [
  ...OUTER_TOWER_FEATURES,
  ...RADIANT_BASE_FEATURES.filter((feature) => feature.kind === 'tower'),
  ...DIRE_BASE_FEATURES.filter((feature) => feature.kind === 'tower'),
]

export function isBaseStructure(feature: MapFeature): boolean {
  const name = (feature.name ?? '').toLowerCase()
  if (feature.team === 2) {
    return feature.kind === 'ancient'
      || feature.kind === 'barracks'
      || (feature.kind === 'tower' && (/goodguys_tower3_(top|mid|bot)$/.test(name)
        || /goodguys_tower4(?:$|_)/.test(name)))
  }
  if (feature.team === 3) {
    return feature.kind === 'ancient'
      || (feature.kind === 'tower' && /badguys_tower4(?:$|_)/.test(name))
  }
  return false
}
