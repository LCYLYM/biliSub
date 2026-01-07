#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
哔哩哔哩字幕下载器 Web UI
提供单文件HTML界面，可本地运行，无需服务器
"""
import os
import json
import uuid
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pydantic import BaseModel, Field

from enhanced_bilisub import BiliSubDownloader, SubtitleFormat

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bilisub_ui.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("BiliSubUI")

# 创建FastAPI应用
app = FastAPI(
    title="哔哩哔哩字幕下载器 Web UI",
    description="提供B站视频字幕下载的Web界面",
    version="1.0.0",
)

# 允许跨域请求（仅用于本地开发）
# 限制为本地地址
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# 创建存储目录
OUTPUT_DIR = Path("ui_output")
OUTPUT_DIR.mkdir(exist_ok=True)

# 内存中的任务存储
tasks_db = {}  # task_id -> task_info

# 数据模型
class DownloadRequest(BaseModel):
    urls: List[str] = Field(..., description="视频URL列表")
    formats: List[str] = Field(["srt"], description="输出格式列表")
    output_dir: Optional[str] = Field(None, description="输出目录")
    concurrency: int = Field(3, description="并发数")
    proxy: Optional[str] = Field(None, description="代理设置")
    use_asr: bool = Field(True, description="是否使用语音识别")
    asr_model: str = Field("small", description="语音识别模型")
    asr_lang: str = Field("zh", description="语音识别语言")

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: float
    message: str
    result: Optional[Dict] = None

# 进度回调
class ProgressCallback:
    def __init__(self, task_id):
        self.task_id = task_id
    
    def __call__(self, progress):
        if self.task_id in tasks_db:
            tasks_db[self.task_id]["progress"] = progress
            tasks_db[self.task_id]["updated_at"] = datetime.now().isoformat()

# 后台任务处理
async def process_download_task(task_id: str, request: DownloadRequest):
    try:
        # 更新状态
        tasks_db[task_id]["status"] = "processing"
        tasks_db[task_id]["message"] = "开始处理..."
        
        # 创建任务输出目录
        task_output_dir = OUTPUT_DIR / task_id
        task_output_dir.mkdir(exist_ok=True)
        
        # 配置下载器
        config = {
            "output_formats": request.formats,
            "use_asr": request.use_asr,
            "asr_model": request.asr_model,
            "asr_lang": request.asr_lang,
            "concurrency": request.concurrency,
            "proxy": request.proxy,
            "temp_dir": str(task_output_dir / "temp"),
            "output_dir": str(task_output_dir),
            "callback": ProgressCallback(task_id),
        }
        
        # 创建下载器
        downloader = BiliSubDownloader(config)
        
        # 解析所有URL
        all_tasks = []
        for url in request.urls:
            try:
                parsed_tasks = downloader.parse_input(url)
                all_tasks.extend(parsed_tasks)
            except Exception as e:
                logger.error(f"解析URL失败 {url}: {str(e)}")
        
        if not all_tasks:
            tasks_db[task_id]["status"] = "failed"
            tasks_db[task_id]["message"] = "无法解析任何视频URL"
            return
        
        tasks_db[task_id]["message"] = f"共{len(all_tasks)}个视频，开始下载..."
        
        # 处理任务
        await downloader.process_tasks(all_tasks)
        
        # 收集结果文件
        result_files = []
        download_urls = {}
        
        for task_item in all_tasks:
            if task_item.subs:
                for fmt in request.formats:
                    # 查找生成的文件
                    bvid_dir = task_output_dir / task_item.bvid
                    if bvid_dir.exists():
                        for file_path in bvid_dir.glob(f"*.{fmt}"):
                            rel_path = file_path.relative_to(task_output_dir)
                            result_files.append(str(rel_path))
                            download_urls[str(rel_path)] = f"/download/{task_id}/{rel_path}"
        
        # 更新任务状态
        tasks_db[task_id]["status"] = "completed"
        tasks_db[task_id]["progress"] = 100
        tasks_db[task_id]["message"] = f"完成！成功处理{downloader.stats['success']}个视频"
        tasks_db[task_id]["result"] = {
            "files": result_files,
            "download_urls": download_urls,
            "stats": downloader.stats,
        }
        
        logger.info(f"任务完成: {task_id}")
        
    except Exception as e:
        logger.error(f"任务处理异常: {task_id} - {str(e)}")
        tasks_db[task_id]["status"] = "failed"
        tasks_db[task_id]["message"] = f"错误: {str(e)}"

# HTML界面
HTML_CONTENT = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>哔哩哔哩字幕下载器</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            color: #333;
        }
        
        .container {
            max-width: 900px;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            color: white;
            margin-bottom: 30px;
        }
        
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }
        
        .header p {
            font-size: 1.1em;
            opacity: 0.9;
        }
        
        .card {
            background: white;
            border-radius: 16px;
            padding: 30px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            color: #555;
        }
        
        .form-group textarea,
        .form-group input[type="text"],
        .form-group input[type="number"],
        .form-group select {
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 14px;
            transition: border-color 0.3s;
        }
        
        .form-group textarea:focus,
        .form-group input:focus,
        .form-group select:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .form-group textarea {
            min-height: 120px;
            resize: vertical;
            font-family: monospace;
        }
        
        .checkbox-group {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
            gap: 10px;
        }
        
        .checkbox-item {
            display: flex;
            align-items: center;
        }
        
        .checkbox-item input[type="checkbox"] {
            width: 18px;
            height: 18px;
            margin-right: 6px;
            cursor: pointer;
        }
        
        .checkbox-item label {
            margin: 0;
            cursor: pointer;
            font-weight: normal;
        }
        
        .advanced-options {
            margin-top: 20px;
            padding-top: 20px;
            border-top: 2px solid #f0f0f0;
        }
        
        .advanced-toggle {
            background: none;
            border: none;
            color: #667eea;
            font-size: 14px;
            cursor: pointer;
            padding: 0;
            text-decoration: underline;
            margin-bottom: 15px;
        }
        
        .advanced-toggle:hover {
            color: #764ba2;
        }
        
        .hidden {
            display: none;
        }
        
        .btn {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 14px 32px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            width: 100%;
            transition: transform 0.2s, box-shadow 0.2s;
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 16px rgba(102, 126, 234, 0.5);
        }
        
        .btn:active {
            transform: translateY(0);
        }
        
        .btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        
        .progress-container {
            margin-top: 20px;
        }
        
        .progress-bar {
            width: 100%;
            height: 30px;
            background: #f0f0f0;
            border-radius: 15px;
            overflow: hidden;
            margin-bottom: 10px;
        }
        
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            transition: width 0.3s;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: 600;
            font-size: 14px;
        }
        
        .status-message {
            text-align: center;
            padding: 12px;
            border-radius: 8px;
            margin-top: 10px;
        }
        
        .status-processing {
            background: #fff3cd;
            color: #856404;
        }
        
        .status-completed {
            background: #d4edda;
            color: #155724;
        }
        
        .status-failed {
            background: #f8d7da;
            color: #721c24;
        }
        
        .results {
            margin-top: 20px;
        }
        
        .results h3 {
            margin-bottom: 15px;
            color: #333;
        }
        
        .file-list {
            list-style: none;
        }
        
        .file-item {
            background: #f8f9fa;
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .file-name {
            flex: 1;
            font-family: monospace;
            font-size: 14px;
            word-break: break-all;
        }
        
        .download-btn {
            background: #667eea;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 14px;
            cursor: pointer;
            margin-left: 10px;
            transition: background 0.2s;
        }
        
        .download-btn:hover {
            background: #764ba2;
        }
        
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }
        
        .stat-item {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }
        
        .stat-value {
            font-size: 24px;
            font-weight: 700;
            color: #667eea;
            margin-bottom: 5px;
        }
        
        .stat-label {
            font-size: 14px;
            color: #666;
        }
        
        .help-text {
            font-size: 13px;
            color: #666;
            margin-top: 5px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎬 哔哩哔哩字幕下载器</h1>
            <p>下载B站视频字幕，支持多种格式和语音识别</p>
        </div>
        
        <div class="card">
            <form id="downloadForm">
                <div class="form-group">
                    <label for="urls">视频URL（每行一个，支持批量）</label>
                    <textarea id="urls" name="urls" placeholder="例如：&#10;https://www.bilibili.com/video/BV1xx411c79H&#10;https://www.bilibili.com/video/BV1Gx411w7sV" required></textarea>
                    <div class="help-text">支持单个或多个B站视频URL，每行一个</div>
                </div>
                
                <div class="form-group">
                    <label>输出格式（可多选）</label>
                    <div class="checkbox-group">
                        <div class="checkbox-item">
                            <input type="checkbox" id="format_srt" value="srt" checked>
                            <label for="format_srt">SRT</label>
                        </div>
                        <div class="checkbox-item">
                            <input type="checkbox" id="format_ass" value="ass">
                            <label for="format_ass">ASS</label>
                        </div>
                        <div class="checkbox-item">
                            <input type="checkbox" id="format_vtt" value="vtt">
                            <label for="format_vtt">VTT</label>
                        </div>
                        <div class="checkbox-item">
                            <input type="checkbox" id="format_json" value="json">
                            <label for="format_json">JSON</label>
                        </div>
                        <div class="checkbox-item">
                            <input type="checkbox" id="format_txt" value="txt">
                            <label for="format_txt">TXT</label>
                        </div>
                        <div class="checkbox-item">
                            <input type="checkbox" id="format_lrc" value="lrc">
                            <label for="format_lrc">LRC</label>
                        </div>
                    </div>
                </div>
                
                <div class="advanced-options">
                    <button type="button" class="advanced-toggle" onclick="toggleAdvanced()">
                        <span id="advancedToggleText">显示高级选项 ▼</span>
                    </button>
                    
                    <div id="advancedContent" class="hidden">
                        <div class="form-group">
                            <label for="concurrency">并发数</label>
                            <input type="number" id="concurrency" name="concurrency" value="3" min="1" max="10">
                            <div class="help-text">同时处理的视频数量，建议1-5</div>
                        </div>
                        
                        <div class="form-group">
                            <label for="proxy">代理设置（可选）</label>
                            <input type="text" id="proxy" name="proxy" placeholder="例如：http://127.0.0.1:7890">
                            <div class="help-text">如需使用代理，填写代理地址</div>
                        </div>
                        
                        <div class="form-group">
                            <div class="checkbox-item">
                                <input type="checkbox" id="use_asr" checked>
                                <label for="use_asr">无字幕时使用语音识别（需要ffmpeg）</label>
                            </div>
                        </div>
                        
                        <div class="form-group">
                            <label for="asr_model">语音识别模型</label>
                            <select id="asr_model" name="asr_model">
                                <option value="tiny">Tiny (最快，精度较低)</option>
                                <option value="base">Base (快，精度一般)</option>
                                <option value="small" selected>Small (推荐，精度较高)</option>
                                <option value="medium">Medium (慢，精度高)</option>
                                <option value="large">Large (最慢，精度最高)</option>
                            </select>
                            <div class="help-text">首次使用会自动下载模型文件</div>
                        </div>
                        
                        <div class="form-group">
                            <label for="asr_lang">语音识别语言</label>
                            <select id="asr_lang" name="asr_lang">
                                <option value="zh" selected>中文</option>
                                <option value="en">英文</option>
                                <option value="ja">日文</option>
                                <option value="ko">韩文</option>
                            </select>
                        </div>
                    </div>
                </div>
                
                <button type="submit" class="btn" id="submitBtn">开始下载</button>
            </form>
            
            <div id="progressContainer" class="progress-container hidden">
                <div class="progress-bar">
                    <div class="progress-fill" id="progressFill" style="width: 0%">0%</div>
                </div>
                <div class="status-message" id="statusMessage"></div>
            </div>
        </div>
        
        <div id="resultsCard" class="card hidden">
            <div class="results">
                <h3>下载结果</h3>
                <div class="stats" id="stats"></div>
                <div style="margin-top: 20px;">
                    <h4 style="margin-bottom: 10px;">文件列表</h4>
                    <ul class="file-list" id="fileList"></ul>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let currentTaskId = null;
        let pollInterval = null;
        
        function toggleAdvanced() {
            const content = document.getElementById('advancedContent');
            const text = document.getElementById('advancedToggleText');
            if (content.classList.contains('hidden')) {
                content.classList.remove('hidden');
                text.textContent = '隐藏高级选项 ▲';
            } else {
                content.classList.add('hidden');
                text.textContent = '显示高级选项 ▼';
            }
        }
        
        document.getElementById('downloadForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            // 获取表单数据
            const urls = document.getElementById('urls').value.trim().split('\n').filter(u => u.trim());
            if (urls.length === 0) {
                alert('请输入至少一个视频URL');
                return;
            }
            
            // 获取选中的格式
            const formats = [];
            document.querySelectorAll('input[type="checkbox"][value]').forEach(cb => {
                if (cb.id.startsWith('format_') && cb.checked) {
                    formats.push(cb.value);
                }
            });
            
            if (formats.length === 0) {
                alert('请至少选择一种输出格式');
                return;
            }
            
            const data = {
                urls: urls,
                formats: formats,
                concurrency: parseInt(document.getElementById('concurrency').value) || 3,
                proxy: document.getElementById('proxy').value.trim() || null,
                use_asr: document.getElementById('use_asr').checked,
                asr_model: document.getElementById('asr_model').value,
                asr_lang: document.getElementById('asr_lang').value,
            };
            
            // 禁用提交按钮
            const submitBtn = document.getElementById('submitBtn');
            submitBtn.disabled = true;
            submitBtn.textContent = '处理中...';
            
            // 显示进度容器
            const progressContainer = document.getElementById('progressContainer');
            progressContainer.classList.remove('hidden');
            updateProgress(0, '正在创建任务...', 'processing');
            
            // 隐藏结果卡片
            document.getElementById('resultsCard').classList.add('hidden');
            
            try {
                // 提交任务
                const response = await fetch('/api/download', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(data),
                });
                
                if (!response.ok) {
                    throw new Error('提交任务失败');
                }
                
                const result = await response.json();
                currentTaskId = result.task_id;
                
                // 开始轮询任务状态
                startPolling();
                
            } catch (error) {
                alert('错误: ' + error.message);
                submitBtn.disabled = false;
                submitBtn.textContent = '开始下载';
                progressContainer.classList.add('hidden');
            }
        });
        
        function startPolling() {
            if (pollInterval) {
                clearInterval(pollInterval);
            }
            
            pollInterval = setInterval(async () => {
                try {
                    const response = await fetch(`/api/status/${currentTaskId}`);
                    if (!response.ok) {
                        throw new Error('获取状态失败');
                    }
                    
                    const status = await response.json();
                    updateProgress(status.progress, status.message, status.status);
                    
                    if (status.status === 'completed') {
                        clearInterval(pollInterval);
                        displayResults(status.result);
                        document.getElementById('submitBtn').disabled = false;
                        document.getElementById('submitBtn').textContent = '开始下载';
                    } else if (status.status === 'failed') {
                        clearInterval(pollInterval);
                        document.getElementById('submitBtn').disabled = false;
                        document.getElementById('submitBtn').textContent = '开始下载';
                    }
                } catch (error) {
                    console.error('轮询错误:', error);
                }
            }, 1000);
        }
        
        function updateProgress(progress, message, status) {
            const progressFill = document.getElementById('progressFill');
            const statusMessage = document.getElementById('statusMessage');
            
            progressFill.style.width = progress + '%';
            progressFill.textContent = Math.round(progress) + '%';
            
            statusMessage.textContent = message;
            statusMessage.className = 'status-message status-' + status;
        }
        
        function displayResults(result) {
            const resultsCard = document.getElementById('resultsCard');
            const stats = document.getElementById('stats');
            const fileList = document.getElementById('fileList');
            
            // 显示统计信息 - 使用安全的DOM操作
            stats.innerHTML = '';
            const statsData = [
                { value: result.stats.total_videos || 0, label: '总视频数' },
                { value: result.stats.success || 0, label: '成功' },
                { value: result.stats.failed || 0, label: '失败' },
                { value: result.stats.asr_used || 0, label: '使用ASR' }
            ];
            
            statsData.forEach(stat => {
                const statItem = document.createElement('div');
                statItem.className = 'stat-item';
                
                const statValue = document.createElement('div');
                statValue.className = 'stat-value';
                statValue.textContent = stat.value;
                
                const statLabel = document.createElement('div');
                statLabel.className = 'stat-label';
                statLabel.textContent = stat.label;
                
                statItem.appendChild(statValue);
                statItem.appendChild(statLabel);
                stats.appendChild(statItem);
            });
            
            // 显示文件列表
            fileList.innerHTML = '';
            if (result.files && result.files.length > 0) {
                result.files.forEach(file => {
                    const li = document.createElement('li');
                    li.className = 'file-item';
                    
                    const fileName = document.createElement('span');
                    fileName.className = 'file-name';
                    fileName.textContent = file;
                    
                    const downloadBtn = document.createElement('button');
                    downloadBtn.className = 'download-btn';
                    downloadBtn.textContent = '下载';
                    downloadBtn.onclick = () => {
                        window.location.href = result.download_urls[file];
                    };
                    
                    li.appendChild(fileName);
                    li.appendChild(downloadBtn);
                    fileList.appendChild(li);
                });
            } else {
                fileList.innerHTML = '<li class="file-item"><span class="file-name">没有生成文件</span></li>';
            }
            
            resultsCard.classList.remove('hidden');
        }
    </script>
</body>
</html>
'''

# API端点
@app.get("/", response_class=HTMLResponse)
async def root():
    """返回单文件HTML界面"""
    return HTML_CONTENT

@app.post("/api/download")
async def create_download_task(
    request: DownloadRequest,
    background_tasks: BackgroundTasks
):
    """创建下载任务"""
    # 生成任务ID
    task_id = str(uuid.uuid4())
    
    # 创建任务记录
    tasks_db[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "progress": 0,
        "message": "任务已创建",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    
    # 启动后台任务
    background_tasks.add_task(process_download_task, task_id, request)
    
    logger.info(f"创建新任务: {task_id} - {len(request.urls)} 个URL")
    
    return {
        "task_id": task_id,
        "status": "pending",
        "message": "任务已创建",
    }

@app.get("/api/status/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态"""
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return tasks_db[task_id]

@app.get("/download/{task_id}/{file_path:path}")
async def download_file(task_id: str, file_path: str):
    """下载文件"""
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 验证file_path，防止目录遍历攻击
    # 移除任何路径遍历字符
    import os.path
    file_path = os.path.normpath(file_path)
    if file_path.startswith('..') or file_path.startswith('/') or '\\' in file_path:
        raise HTTPException(status_code=400, detail="无效的文件路径")
    
    file_full_path = OUTPUT_DIR / task_id / file_path
    
    # 确保文件路径在允许的目录内
    try:
        file_full_path = file_full_path.resolve()
        allowed_dir = (OUTPUT_DIR / task_id).resolve()
        if not str(file_full_path).startswith(str(allowed_dir)):
            raise HTTPException(status_code=403, detail="禁止访问")
    except Exception:
        raise HTTPException(status_code=400, detail="无效的文件路径")
    
    if not file_full_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # 确定媒体类型
    media_type = "application/octet-stream"
    if file_path.endswith(".srt"):
        media_type = "application/x-subrip"
    elif file_path.endswith(".ass"):
        media_type = "text/plain"
    elif file_path.endswith(".vtt"):
        media_type = "text/vtt"
    elif file_path.endswith(".json"):
        media_type = "application/json"
    elif file_path.endswith(".txt"):
        media_type = "text/plain"
    elif file_path.endswith(".lrc"):
        media_type = "text/plain"
    
    return FileResponse(
        path=file_full_path,
        filename=file_full_path.name,
        media_type=media_type
    )

def main():
    """主函数"""
    import shutil
    
    # 检查依赖
    try:
        import whisper
    except ImportError:
        logger.warning("未安装openai-whisper，语音识别功能将不可用")
        logger.warning("安装方法: pip install openai-whisper")
    
    # 检查ffmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        logger.warning("未检测到ffmpeg，语音识别功能将不可用")
        logger.warning("请安装ffmpeg：https://ffmpeg.org/download.html")
    
    print("\n" + "="*60)
    print("哔哩哔哩字幕下载器 Web UI")
    print("="*60)
    print("\n启动服务器...")
    print("访问地址: http://localhost:8080")
    print("\n按 Ctrl+C 停止服务器\n")
    
    # 启动服务器（仅绑定到本地回环地址）
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")

if __name__ == "__main__":
    main()
