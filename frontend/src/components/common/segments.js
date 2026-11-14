import {SegmentStatus,ErrorKind} from '../../constants/enums.js';
import {esc,fmt,date} from '../../utils/dom.js';
export function progressBar(j){
 const p=j.progress||{total:j.segments_total||0,completed:0,failed:0,running:0,pending:0,percent:0};
 const reusable=j.segments_reused||0;
 return '<div class="segment-progress"><div class="segment-meter" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="'+esc(p.percent)+'"><span style="width:'+esc(p.percent)+'%"></span></div><div class="segment-counts"><span><strong>'+esc(p.completed)+'</strong>/'+esc(p.total)+' 段已完成 · '+esc(p.percent)+'%</span><span>失败 '+esc(p.failed)+' · 处理中 '+esc(p.running)+' · 待处理 '+esc(p.pending)+'</span></div><p class="hint">'+(reusable?'本次恢复沿用 '+esc(reusable)+' 个已完成段的检查点，计数不会重复汇入。':'每个分段完成后立即保存，停机后从断点继续。')+'</p></div>';
}
export function segmentDetail(j){
 const rows=j.segments||[];
 if(!rows.length)return '';
 return '<div class="table-wrap"><table><thead><tr><th>分段</th><th>记录范围</th><th>状态</th><th>进区</th><th>完成</th><th>尝试</th><th>失败原因</th><th>更新时间</th></tr></thead><tbody>'+rows.map(s=>'<tr><td>'+esc(s.label)+'</td><td>'+esc(s.start_index+1)+'–'+esc(s.end_index)+'</td><td><span class="status '+esc(s.status)+'">'+esc(SegmentStatus[s.status]||s.status)+'</span></td><td>'+esc(fmt(s.visitors))+'</td><td>'+esc(fmt(s.completed))+'</td><td>'+esc(s.attempts)+'</td><td>'+(s.error?esc((ErrorKind[s.error_kind]||'')+'：'+s.error):'—')+'</td><td>'+esc(date(s.updated_at))+'</td></tr>').join('')+'</tbody></table></div>';
}
export const failedSegments=j=>(j.segments||[]).filter(s=>s.status==='failed');
