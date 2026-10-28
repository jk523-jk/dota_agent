import type { MapFeature } from './types'

// Static Dota map anchors (game world coordinates + 16384 replay origin).
// Source mapdata: https://github.com/devilesk/dota-map-coordinates/blob/master/mapdata.json
export const RADIANT_BASE_FEATURES: MapFeature[] = [
  { feature_id: 'radiant-base:tower3_top', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower3_top', x: 9792, y: 12976 },
  { feature_id: 'radiant-base:tower3_mid', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower3_mid', x: 11744, y: 12240 },
  { feature_id: 'radiant-base:tower3_bot', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower3_bot', x: 12432, y: 10272 },
  { feature_id: 'radiant-base:barracks_melee_top', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_melee_rax_top', x: 10048, y: 12626 },
  { feature_id: 'radiant-base:barracks_range_top', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_range_rax_top', x: 9540, y: 12625 },
  { feature_id: 'radiant-base:barracks_melee_mid', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_melee_rax_mid', x: 11712, y: 11832 },
  { feature_id: 'radiant-base:barracks_range_mid', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_range_rax_mid', x: 11324, y: 12185 },
  { feature_id: 'radiant-base:barracks_melee_bot', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_melee_rax_bot', x: 12104, y: 10024 },
  { feature_id: 'radiant-base:barracks_range_bot', kind: 'barracks', team: 2, name: 'npc_dota_goodguys_range_rax_bot', x: 12105, y: 10531 },
  { feature_id: 'radiant-base:tower4_top', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower4_top', x: 10672, y: 11520 },
  { feature_id: 'radiant-base:tower4_bot', kind: 'tower', team: 2, name: 'npc_dota_goodguys_tower4_bot', x: 10992, y: 11192 },
  { feature_id: 'radiant-base:ancient', kind: 'ancient', team: 2, name: 'npc_dota_goodguys_fort', x: 10464, y: 11032 },
]

export function isRadiantBaseStructure(feature: MapFeature): boolean {
  if (feature.team !== 2) return false
  const name = (feature.name ?? '').toLowerCase()
  return feature.kind === 'ancient'
    || feature.kind === 'barracks'
    || (feature.kind === 'tower' && (/goodguys_tower3_(top|mid|bot)$/.test(name)
      || /goodguys_tower4(?:$|_)/.test(name)))
}
