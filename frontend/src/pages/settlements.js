import {state} from '../stores/session.js';
import {table} from '../components/common/table.js';
import {field, select} from '../components/common/fields.js';
import {VENUES, CHANNELS} from '../constants/enums.js';
import {money} from '../utils/format.js';

export function render() {
  return `<div class="split"><section class="panel"><h2>场次票款，逐笔留据</h2>
<p>每条为独立场次；渠道为主售票渠道。金额录入整数分，避免浮点分币误差。票款留空表示缺失，将由凭证补账推导，和真实零元区分。</p>
${table(['场次', '日期', '场馆 / 演出', '渠道', '票款 / 分', '净结算 ¥', '核验/座位'],
    state.settlements.map(r => [r.code, r.day, r.venue + ' / ' + r.play, r.channel,
      r.gross_cents === null ? '缺失' : r.gross_cents,
      r.gross_cents === null ? '缺失' : money(r.gross_cents - r.refund_cents - r.fee_cents),
      r.checked + '/' + r.seats]))}</section>
<aside class="side"><h2>录入 / 修订场次</h2>
<form id="settlement">${field('记录ID（修订填写）', 'record_id', 'number', 'min=1')}${field('场次编号', 'code', 'text', 'required')}${field('业务日期', 'day', 'date', 'required value="2026-11-15"')}${select('场馆', 'venue', VENUES.map(x => [x, x]))}${field('演出名称', 'play', 'text', 'required')}${select('主售票渠道', 'channel', CHANNELS.map(x => [x, x]))}${['gross_cents:票款 / 分（留空=缺失）', 'refund_cents:退款 / 分', 'fee_cents:渠道费 / 分', 'seats:座位', 'checked:核验人数'].map(x => { const [n, l] = x.split(':'); return field(l, n, 'number', 'min=0 step=1' + (n === 'gross_cents' ? '' : ' required')); }).join('')}<button class="primary">保存结算行</button></form>
<p class="notice">修订会递增记录修订号，使求解中的补账结果过期。缺失票款场次不能带退款或渠道费。</p>
</aside></div>`;
}
