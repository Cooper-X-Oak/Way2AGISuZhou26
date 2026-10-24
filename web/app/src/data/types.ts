export type Json = null|boolean|number|string|Json[]|{[key:string]:Json};
export type StageId = 'L0'|'L1'|'L2'|'L2C'|'L3'|'L3P'|'L4'|'PACK';
export type CheckStatus = 'passed'|'failed'|'blocked'|'needs_review'|'not_run';
export interface Provenance {run_id:string; files:Record<string,string>}
export interface Envelope {schema_version:string; generated_from:Provenance}
export interface Evidence {judged_by?:string; confidence?:number; evidence?:string[]; open?:string[]; open_ref?:string}
export interface Violation {rule_id?:string; code?:string; matched_term?:string; path?:string; offset?:number; message?:string}
export interface Check {passed:boolean; layers:{layer:string;status:CheckStatus;approved_by?:string|null;required?:boolean;violations?:Violation[]}[]; violations:Violation[]}
export interface History {stage:StageId;timestamp:string;attempt?:number;revision?:number;status?:string;violations?:Violation[];check?:Check;approved_by?:string|null;judged_by?:string;retry?:boolean;duration_seconds?:number;backtrack?:{from:StageId;to:StageId;reason:string;zone_ids:string[]};count?:number;demo_configuration?:unknown;failed_items?:Item[]}
export interface Route {week:number;day:number;person:number;order:number;date:null;route_distance_m:number}
export interface Supplier {partner_id:string|null;status:string;zone_id:string;distance_m:number}
export interface Item extends Evidence {commercial_conditions?:Json;covered_terminal_ids?:string[];recommended_zone_ids?:string[];poi_id?:string;name?:string;channel_id?:string;zone_id?:string;score?:number;grade?:string;next_action?:string;reason?:string;score_components?:Record<string,number>;priority?:string;scene_ids?:string[];selling_point_ids?:string[];rank?:number;selected?:boolean;tier?:string;type?:string;counts?:Record<string,number>;intensity?:string;strategy?:string;partner_id?:string;recommendation?:string;coverage_count?:number;target?:string;pitch?:string;table_card?:string;compliance?:{passed:boolean;hits:Violation[]};route?:Route|null;suggested_supplier?:Supplier|null;pitch_ref?:{file:string;stage:string;target:string}|null;trace_ref?:string|null}
export interface Brief extends Evidence {radius_m:number;delivery_people:number;weeks:number;request:string;profile_id:string}
export interface Stage extends Evidence {decision_source?:string;stage:StageId;available:boolean;accepted:boolean;decision:string|null;items:Item[];check:Check|null;history:History[];brief?:Brief;item_notes?:Record<string,string[]>}
export interface Point extends Item {poi_id:string;name:string;lat:number;lon:number;category?:string;coordinate_system?:string}
export interface Grid extends Item {zone_id:string;geometry:{type:string;coordinates:number[][][]}}
export interface Area {lat:number;lon:number;display_name:string;location_note?:string;coordinate_system:string}
export interface MapData extends Envelope {site:Area;grids:Grid[];terminals:Point[];competitors:Point[];availability:Point[];partners:Point[];coverage:{source?:string;fetched_at?:string;truncated?:boolean};open?:string[]}
export interface Summary extends Envelope {conclusions:Record<StageId,string|null>;checks:Record<StageId,{layer:string;status:CheckStatus}[]>;assumptions:Json;missing_artifacts:string[];scenario_id:string;area_id:string;area:Area;judged_by:string;demo_replay:string|null;status:{run_id:string;status:string;completed_stages:StageId[];backtracks:number};counts:{grades:Record<string,number>;channels:Record<string,number>;zones:number;selected_zones:number;competition_intensity:Record<string,number>;recommended_partners:number;recruitment_gaps:number|null};terminal_count_source:string;route_date_note:string;product_profile:{id:string;product:{name:string};selling_points?:{id:string;name:string}[]};memo?:string|{paragraphs?: (string|{text:string})[];text?:string;references?:{number?:number;poi_id?:string;zone_id?:string;stage?:StageId}[]}}
export interface Terminals extends Envelope {recruitment_gaps?:{terminal_count:number;terminal_ids:string[];zone_ids:string[]}|null;available:boolean;items:Item[];days:{week:number;day:number;person:number;poi_ids:string[]}[];backlog_ids:string[];planning_note:string}
export interface Scenario {id:string;summary:Summary;map:MapData;stages:Stage[];terminals:Terminals}
export interface Index {schema_version:string;generated_from:{runs:Provenance[]};scenarios:{id:string;area_id:string;demo_replay:string|null;summary:string}[]}
export interface TraceNode {data:Item;judged_by?:string;check?:Check;source?:{quote:string;file?:string;source?:string}}
export interface Trace {terminal?:TraceNode;score_components?:{data:Record<string,number>};zone?:TraceNode;channel?:TraceNode;competition?:TraceNode;supply?:{data:Supplier};scenes?:TraceNode[];selling_points?:TraceNode[]}
declare global {interface Window {__BUNDLE__?:Record<string,unknown>}}
