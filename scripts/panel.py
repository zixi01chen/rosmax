#!/usr/bin/env python3
"""rosmax 主机端面板：浏览器打开 http://<本机IP>:8090 看实时性能。

用法（在电脑上）:
  python3 scripts/panel.py            # 起板端压测(1组×3节点@30Hz×1KB) + 面板服务 :8090
  python3 scripts/panel.py 5 6 1024 30 # 自定义 组数/节点数/字节/频率
  python3 scripts/panel.py stop       # 停板端压测（面板 Ctrl-C 退出即可）

依赖: python3 标准库 + sshpass（拉取板端 status.json）
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml  # pip install pyyaml（板卡注册表解析）

# 板卡注册表：scripts/boards.yaml（新增板子 = 加条目，无需改代码）
_REG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boards.yaml")


def load_registry():
    with open(_REG_PATH, encoding="utf-8") as f:
        reg = yaml.safe_load(f)
    return reg.get("default"), reg.get("boards", {})


DEFAULT_BOARD, BOARDS = load_registry()
STATE = {"json": "{}", "err": None, "ts": None}
BOARD_NAME = None
SSH = []


def use_board(name):
    """按注册表名字选择板卡，构建 SSH 命令前缀"""
    global BOARD_NAME, SSH
    b = BOARDS[name]
    BOARD_NAME = name
    SSH = ["sshpass", "-p", b["password"], "ssh",
           "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=/dev/null",  # 板常重启换 host key，避免 known_hosts 冲突阻断监控
           f"{b['user']}@{b['host']}"]

PAGE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>rosmax 实时性能面板</title>
<style>
 body{background:#0d1117;color:#e6edf3;font-family:ui-monospace,Consolas,monospace;
      margin:0;padding:24px}
 h1{font-size:18px;margin:0 0 4px}
 .sub{color:#8b949e;font-size:12px;margin-bottom:16px}
 table{border-collapse:collapse;width:100%;margin-bottom:20px}
 th,td{padding:6px 10px;text-align:right;border-bottom:1px solid #21262d;font-size:13px}
 th{color:#8b949e;font-weight:600;text-align:right}
 td.name,th.name{text-align:left}
 .ok{color:#3fb950}.warn{color:#d29922}.bad{color:#f85149}
 .cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
 .card{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:10px 16px}
 .card .v{font-size:20px;font-weight:700}
 .card .k{font-size:11px;color:#8b949e}
 canvas{background:#161b22;border:1px solid #21262d;border-radius:8px}
 #err{color:#f85149;font-size:12px;white-space:pre}
</style></head><body>
<h1>rosmax 实时性能面板 <span style="color:#8b949e;font-size:12px" id="board"></span></h1>
<div class="sub">板端跑 ROS2 压测节点 · 电脑端看面板 · 数据每秒拉取 · <span id="clock">-</span></div>
<div class="cards">
 <div class="card"><div class="v" id="c-nodes">-</div><div class="k">节点总数</div></div>
 <div class="card"><div class="v" id="c-topics">-</div><div class="k">话题总数</div></div>
 <div class="card"><div class="v" id="c-rate">-</div><div class="k">每话题频率</div></div>
 <div class="card"><div class="v" id="c-total">-</div><div class="k">总吞吐 msg/s</div></div>
 <div class="card"><div class="v" id="c-loss">-</div><div class="k">累计丢包</div></div>
 <div class="card"><div class="v" id="c-lossrate">-</div><div class="k">丢包率</div></div>
 <div class="card"><div class="v" id="c-cpu">-</div><div class="k">CPU 占用(压测工具)</div></div>
 <div class="card"><div class="v" id="c-mem">-</div><div class="k">内存占用(工具 RSS)</div></div>
 <div class="card"><div class="v" id="c-ddr-r">-</div><div class="k">DDR 读</div></div>
 <div class="card"><div class="v" id="c-ddr-w">-</div><div class="k">DDR 写</div></div>
 <div class="card"><div class="v" id="c-ddr-t">-</div><div class="k">DDR 总带宽</div></div>
 <div class="card"><div class="v" id="c-temp">-</div><div class="k">板温</div></div>
 <div class="card"><div class="v" id="c-load">-</div><div class="k">板负载</div></div>
</div>
<div class="cards">
 <div class="card"><div class="k">组数 G</div>
  <input id="in-g" type="number" value="1" min="1" max="200" style="width:60px;background:#0d1117;color:#e6edf3;border:1px solid #30363d"></div>
 <div class="card"><div class="k">组内节点数 N（2~50）</div>
  <input id="in-n" type="number" value="3" min="2" max="50" style="width:60px;background:#0d1117;color:#e6edf3;border:1px solid #30363d"></div>
 <div class="card"><div class="k">频率 Hz</div>
  <input id="in-rate" type="number" value="30" min="1" max="10000" style="width:70px;background:#0d1117;color:#e6edf3;border:1px solid #30363d"></div>
 <div class="card"><div class="k">字节（≥42）</div>
  <input id="in-size" type="number" value="1024" min="42" max="10485760" style="width:80px;background:#0d1117;color:#e6edf3;border:1px solid #30363d"></div>
 <div class="card"><button onclick="applyGN()" style="background:#238636;color:#fff;border:none;padding:8px 14px;border-radius:6px;cursor:pointer">应用 G×N</button>
  <button onclick="scale(+1,0)" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;padding:8px 10px;border-radius:6px;cursor:pointer;margin-left:6px">+1 组</button>
  <button onclick="scale(-1,0)" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;padding:8px 10px;border-radius:6px;cursor:pointer;margin-left:6px">-1 组</button>
  <button onclick="scale(0,+1)" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;padding:8px 10px;border-radius:6px;cursor:pointer;margin-left:6px">+1 节点/组</button>
  <button onclick="scale(0,-1)" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;padding:8px 10px;border-radius:6px;cursor:pointer;margin-left:6px">-1 节点/组</button></div>
</div>
<div class="sub" style="margin-top:14px">设备上的全部话题（含外部系统；同 domain 可见）</div>
<table id="tt"><thead><tr>
 <th class="name">话题</th><th class="name">类型</th><th>pubs</th><th>subs</th><th>来源</th>
</tr></thead><tbody id="tt-body"></tbody></table>
<div style="display:flex;gap:10px;align-items:center;margin:-10px 0 18px">
 <button onclick="tpage(-1)" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;padding:6px 12px;border-radius:6px;cursor:pointer">◀ 上一页</button>
 <span id="tpage-ind" style="font-size:12px;color:#8b949e">1/1</span>
 <button onclick="tpage(1)" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;padding:6px 12px;border-radius:6px;cursor:pointer">下一页 ▶</button>
 <span class="sub" style="margin:0" id="tt-count"></span>
</div>
<div class="sub">压测实时性能（每条话题一行 = 单来源→目的地；按延迟从高到低排列，翻页查看全部）<span id="data-fresh" style="color:#3fb950">· 实时</span></div>
<table id="t"><thead><tr>
 <th class="name">组</th><th class="name">话题</th><th class="name">发布→接收</th><th>recv</th>
 <th title="该话题的真实接收频率">接收Hz</th><th>mean</th><th>p50</th>
 <th>p99</th><th>max</th><th>loss</th><th>丢包率</th><th>乱序</th>
</tr></thead><tbody></tbody></table>
<div style="display:flex;gap:10px;align-items:center;margin:-10px 0 18px">
 <button onclick="spage(-1)" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;padding:6px 12px;border-radius:6px;cursor:pointer">◀ 上一页</button>
 <span id="spage-ind" style="font-size:12px;color:#8b949e">1/1</span>
 <button onclick="spage(1)" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;padding:6px 12px;border-radius:6px;cursor:pointer">下一页 ▶</button>
 <span class="sub" style="margin:0" id="s-count">共 0 条话题</span>
</div>
<canvas id="chart" width="900" height="180"></canvas>
<div class="sub" style="margin-top:8px">p50 时延趋势（当前排序前 10 条，各一条线，60 秒窗口）</div>
<pre id="err"></pre>
<script>
const HIST={};
const TR={};    // 压测表行缓存 key→<tr>
const TROW={};  // 全话题表行缓存 topic→<tr>
let TPAGE=1, SPAGE=1;
const TT_PAGE_SIZE=10;
let SMOUNT="", TMOUNT="";      // 已挂载页的键序列（变化才重排 DOM）
let TOP10KEYS=[];              // 时序图：延迟最高的前 10 条
function stressColor(i,v){ return i===9?(v==="0"?"ok":"bad"):undefined; }
function topicColor(i,v){ return i===4?(v==="外部"?"warn":"ok"):undefined; }
function setCells(cells,vals,colorOf){
 for(let i=0;i<cells.length;i++){
  const c=cells[i],v=vals[i];
  if(c.v!==v){c.el.textContent=v;c.v=v;}
  if(colorOf){ const w=colorOf(i,v); const target=(w!==undefined&&w!=="")?w:c.base;
   if(c.cls!==target){c.el.className=target;c.cls=target;} }
 }
}
function us(x){return (x/1000).toFixed(1)+"μs"}
// 用户正在聚焦或已编辑未应用的输入框，轮询不得覆盖（否则数字被打回，永远填不进）
function setInput(id,v){
  const el=document.getElementById(id);
  if(!el||document.activeElement===el||el.dataset.dirty==="1")return;
  el.value=v;
}
window.addEventListener("DOMContentLoaded",()=>{
  for(const id of ["in-g","in-n","in-rate","in-size"]){
    const el=document.getElementById(id);
    if(el)el.addEventListener("input",()=>{el.dataset.dirty="1"});
  }
});
function cls(el,v){el.className=v>0?"ok":"warn"}
async function poll(){
 try{
  const r=await fetch("/stats.json");const d=await r.json();
  document.getElementById("err").textContent="";
  document.getElementById("board").textContent="· 板 "+(d.cfg_board||"");
  document.getElementById("clock").textContent=d.ts||"-";
  const age=d.data_age_s;
  const df=document.getElementById("data-fresh");
  if(df){
   if(age!==null&&age!==undefined&&age>10){
    df.textContent="· 历史数据（"+(d.data_ts||"-")+" 起，约 "+age+"s 前，等待新拓扑数据…）";
    df.style.color="#d29922";
   }else{
    df.textContent="· 实时";df.style.color="#3fb950";
   }
  }
  const c=d.config||{};
  const gN=c.groups||1, nN=c.nodes_per_group||0;
  document.getElementById("c-nodes").textContent=gN*nN;
  document.getElementById("c-topics").textContent=gN*nN*(nN>1?nN-1:0);
  document.getElementById("c-rate").textContent=c.rate_hz?c.rate_hz+"Hz/话题":"-";
  const tot=(d.nodes||[]).reduce((s,t)=>s+(t.rate||0),0);
  document.getElementById("c-total").textContent=tot.toFixed(0);
  document.getElementById("c-total").textContent=tot.toFixed(0);
  const loss=(d.nodes||[]).reduce((s,t)=>s+(t.loss||0),0);
  const recvAll=(d.nodes||[]).reduce((s,t)=>s+(t.recv||0),0);
  const lossRate= (recvAll+loss)>0 ? loss/(recvAll+loss)*100 : 0;
  const el=document.getElementById("c-loss");el.textContent=loss;
  el.className=loss>0?"bad":"ok";
  const er=document.getElementById("c-lossrate");
  er.textContent=lossRate.toFixed(3)+"%";
  er.className=lossRate>1?"bad":(lossRate>0.01?"warn":"ok");
  document.getElementById("c-temp").textContent=(d.temp_c??"-")+"°C";
  document.getElementById("c-load").textContent=(d.load||"-").split(",")[0];
  document.getElementById("c-cpu").textContent=d.cpu_percent!==null&&d.cpu_percent!==undefined?d.cpu_percent+"%":"-";
  const m=d.mem||{};
  document.getElementById("c-mem").textContent=m.rss_mb?m.rss_mb+"MB RSS":"-";
  const dd=d.ddr||{};
  if(dd.read_mbps===undefined||dd.read_mbps===null){["c-ddr-r","c-ddr-w","c-ddr-t"].forEach(i=>document.getElementById(i).textContent="-");}
  else{
    const t=(dd.total_mbps??((dd.read_mbps||0)+(dd.write_mbps||0))),gb=t>=1024,
          f=v=>gb?(v/1024).toFixed(1):String(Math.round(v)),u=gb?" GB/s":" MB/s";
    document.getElementById("c-ddr-r").textContent=f(dd.read_mbps)+u;
    document.getElementById("c-ddr-w").textContent=f(dd.write_mbps||0)+u;
    document.getElementById("c-ddr-t").textContent=f(t)+u;
  }
  setInput("in-g",c.groups??1);
  setInput("in-n",c.nodes_per_group??3);
  setInput("in-rate",c.rate_hz??30);
  setInput("in-size",c.size_bytes??1024);
  // 大拓扑：全量按 p50 降序，分页展示（每页 10 条，时序图画全局延迟最高的前 10 条）
  const all=(d.nodes||[]);
  const top=[...all].sort((a,b)=>(b.p50||0)-(a.p50||0));
  const sTotalPages=Math.max(1,Math.ceil(all.length/TT_PAGE_SIZE));
  if(SPAGE>sTotalPages)SPAGE=sTotalPages;
  if(SPAGE<1)SPAGE=1;
  const pageKeys=top.slice((SPAGE-1)*TT_PAGE_SIZE,SPAGE*TT_PAGE_SIZE).map(t=>t.group+"/"+t.topic);
  const live=new Set((d.nodes||[]).map(t=>t.group+"/"+t.topic));
  const tb=document.querySelector("#t tbody");
  // 数据模型更新：全部话题存入行缓存 _data（未挂载行不触 DOM）
  for(const t of top){
   const key=t.group+"/"+t.topic;
   let tr=TR[key];
   const recvAll=t.recv+t.loss;
   const lossRate=recvAll>0?(t.loss/recvAll*100).toFixed(2)+"%":"0.00%";
   const cells=[t.group,t.topic,
    t.src_node.split("_").pop()+"→"+t.dst_node.split("_").pop(),
    String(t.recv),t.rate+"Hz",us(t.mean),us(t.p50),us(t.p99),us(t.max),
    String(t.loss),lossRate,String(t.disorder)];
   if(!tr){
    tr=document.createElement("tr");
    tr.innerHTML=`<td class="name"></td><td class="name"></td><td class="name"></td>`+
     `<td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td>`;
    tr._key=key;
    tr._cells=[...tr.children].map(td=>({el:td,v:undefined,base:td.className,cls:td.className}));
    TR[key]=tr;
   }
   tr._data=cells;
   (HIST[key]=HIST[key]||[]).push(t.p50);
   if(HIST[key].length>60)HIST[key].shift();
  }
  for(const k of Object.keys(TR)){
   if(!live.has(k)){ if(TR[k].isConnected) TR[k].remove(); delete TR[k]; }
  }
  // 挂载维护：只摘除不在当前页的行——绝不整表拆除（消除每秒拆建的"一直在刷新"）
  const wantSet=new Set(pageKeys);
  for(const tr of Object.values(TR)){
   if(tr.isConnected && !wantSet.has(tr._key)) tr.remove();
  }
  // 顺序维护：按当前排序 append（对已挂节点是移动，无闪烁）
  for(const k of pageKeys) tb.appendChild(TR[k]);
  // 原地刷新当前页数值
  for(const k of pageKeys){ const tr=TR[k]; if(tr&&tr._data) setCells(tr._cells,tr._data,stressColor); }
  TOP10KEYS=top.slice(0,10).map(t=>t.group+"/"+t.topic);
  document.getElementById("spage-ind").textContent=SPAGE+"/"+sTotalPages;
  document.getElementById("s-count").textContent="共 "+all.length+" 条话题";
  // 全部话题表（同模型，按名称排序分页）
  const tb2=document.querySelector("#tt-body");
  const allT=(d.all_topics||[]).slice().sort((a,b)=>a.name<b.name?-1:1);
  const totalT=allT.length;
  const totalPages=Math.max(1,Math.ceil(totalT/TT_PAGE_SIZE));
  if(TPAGE>totalPages)TPAGE=totalPages;
  if(TPAGE<1)TPAGE=1;
  const tPageKeys=allT.slice((TPAGE-1)*TT_PAGE_SIZE,TPAGE*TT_PAGE_SIZE).map(t=>t.name);
  for(const t of allT){
   let tr=TROW[t.name];
   const cells=[t.name,t.type,String(t.pubs),String(t.subs),t.external?"外部":"压测"];
   if(!tr){
    tr=document.createElement("tr");
    tr.innerHTML=`<td class="name"></td><td class="name"></td><td></td><td></td><td></td>`;
    tr._key=t.name;
    tr._cells=[...tr.children].map(td=>({el:td,v:undefined,base:td.className,cls:td.className}));
    TROW[t.name]=tr;
   }
   tr._data=cells;
  }
  for(const k of Object.keys(TROW)){
   if(!allT.some(t=>t.name===k)){ if(TROW[k].isConnected) TROW[k].remove(); delete TROW[k]; }
  }
  const wantT=new Set(tPageKeys);
  for(const tr of Object.values(TROW)){
   if(tr.isConnected && !wantT.has(tr._key)) tr.remove();
  }
  for(const k of tPageKeys) tb2.appendChild(TROW[k]);
  for(const k of tPageKeys){ const tr=TROW[k]; if(tr&&tr._data) setCells(tr._cells,tr._data,topicColor); }
  document.getElementById("tt-count").textContent="共 "+totalT+" 个话题";
  draw();
 }catch(e){document.getElementById("err").textContent="拉取失败: "+e}
}
function draw(){
 const cv=document.getElementById("chart"),g=cv.getContext("2d");
 g.fillStyle="#0d1117";g.fillRect(0,0,cv.width,cv.height);
 const names=Object.keys(HIST);
 if(!names.length)return;
 let mx=1;names.forEach(n=>HIST[n].forEach(v=>{if(v>mx)mx=v}));
 const colors=["#58a6ff","#3fb950","#d29922","#f85149","#bc8cff","#39d353"];
 const shown=TOP10KEYS.filter(k=>HIST[k]&&HIST[k].length);
 shown.forEach((n,i)=>{
  g.strokeStyle=colors[i%colors.length];g.lineWidth=1.5;g.beginPath();
  const h=HIST[n];
  h.forEach((v,k)=>{
   const x=10+k*(cv.width-20)/60, y=cv.height-10-(v/mx)*(cv.height-30);
   k?g.lineTo(x,y):g.moveTo(x,y);
  });g.stroke();
  const last=h[h.length-1];
  g.fillStyle=colors[i%colors.length];
  g.fillText(`${n} ${us(last)}`,10,14+i*14);
 });
 g.fillStyle="#8b949e";
 g.fillText("0",10,cv.height-2);g.fillText(us(mx),10,24);
}
async function scale(dg,dn){
 const g=Math.max(1,(parseInt(document.getElementById("in-g").value)||1)+dg);
 const n=Math.max(2,(parseInt(document.getElementById("in-n").value)||3)+dn);
 const r=parseInt(document.getElementById("in-rate").value)||200;
 const z=parseInt(document.getElementById("in-size").value)||1024;
 await fetch("/api/topology",{method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({g,n,rate:r,size:z})});
 for(const id of ["in-g","in-n","in-rate","in-size"]){
   const el=document.getElementById(id); if(el)el.dataset.dirty="0";
 }
}
async function applyGN(){
 const g=parseInt(document.getElementById("in-g").value)||1;
 const n=Math.max(2,parseInt(document.getElementById("in-n").value)||3);
 const r=parseInt(document.getElementById("in-rate").value)||200;
 const z=parseInt(document.getElementById("in-size").value)||1024;
 await fetch("/api/topology",{method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({g,n,rate:r,size:z})});
 for(const id of ["in-g","in-n","in-rate","in-size"]){
   const el=document.getElementById(id); if(el)el.dataset.dirty="0";
 }
}
function tpage(d){
 const ind=document.getElementById("tpage-ind").textContent.split("/");
 const total=parseInt(ind[1])||1;
 TPAGE=Math.min(total,Math.max(1,TPAGE+d));
 poll();
}
function spage(d){
 const ind=document.getElementById("spage-ind").textContent.split("/");
 const total=parseInt(ind[1])||1;
 SPAGE=Math.min(total,Math.max(1,SPAGE+d));
 poll();
}
poll();setInterval(poll,1000);
</script></body></html>"""


def pull_loop():
    while True:
        try:
            out = subprocess.check_output(
                SSH + ["cat /root/rosmax_live/status.json"],
                timeout=10, stderr=subprocess.DEVNULL)
            STATE["json"] = out.decode()
            STATE["ts"] = time.strftime("%H:%M:%S")
            STATE["err"] = None
        except Exception as e:
            STATE["err"] = str(e)
        time.sleep(1)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/stats.json":
            body = STATE["json"].encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/topology":
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                g = max(1, int(body.get("g", 1)))
                nn = max(2, min(50, int(body.get("n", 3))))
                rt = max(1, int(body.get("rate", 200)))
                sz = max(42, int(body.get("size", 1024)))
                subprocess.run(SSH + [f"bash /root/rosmax_overlay/topology.sh ensure {g} {nn} {rt} {sz}"],
                               timeout=600)
                out = b'{"ok":true}'
            except Exception as e:
                out = json.dumps({"ok": False, "err": str(e)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        use_board(sys.argv[2] if len(sys.argv) > 2 else DEFAULT_BOARD)
        subprocess.run(SSH + ["bash /root/rosmax_overlay/live_headless.sh stop"])
        print("板端压测已停止")
        return
    # --board NAME：选择板卡注册表中的板子（默认取 default 键）
    board_name = DEFAULT_BOARD
    if "--board" in sys.argv:
        board_name = sys.argv[sys.argv.index("--board") + 1]
    use_board(board_name)
    g_arg = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "stop" and not sys.argv[1].startswith("--") else "1"
    n_arg = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else "3"
    rate = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("-") else "30"
    size = sys.argv[4] if len(sys.argv) > 4 and not sys.argv[4].startswith("-") else "1024"
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8090
    # 设计变更：面板重启不再自动重启板端压测（此前"面板重启→连带重启压测→
    # 反复状态缠绕"的根源）。板端压测由页面"应用 G×N"按钮显式启动。
    print(f"面板已启动（默认 {g_arg} 组 × {n_arg} 节点 @ {rate}Hz）。板端压测未改动——在页面上点「应用 G×N」启动/变更拓扑。")
    threading.Thread(target=pull_loop, daemon=True).start()
    print(f"面板: http://10.112.148.201:{port}  （Ctrl-C 停止面板服务；板端节点继续跑）")
    print(f"停止板端压测: python3 {sys.argv[0]} stop")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    main()
