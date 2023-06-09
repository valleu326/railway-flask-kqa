#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, datetime, json
import numpy as np
from flask import Flask, request, redirect, url_for, render_template, session
import kqa
from unstructured.partition.doc import partition_doc
from unstructured.partition.docx import partition_docx
import fitz
from fproc import crawl_webpage

# 获取全局变量
if os.getenv("DEPLOY_ON_RAILWAY"):
    # 在railway部署：从系统中获取环境变量
    PORT = os.getenv("PORT")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL")
    OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL")
    PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
    SERP_API_KEY = os.getenv("SERP_API_KEY")
    MONGO_URL = os.getenv("MONGO_URL")
else:
    # 在本地部署：读取配置文件中的变量
    with open("./config.json", encoding='utf-8') as config_fid:
        config = json.load(config_fid)  # 读取配置文件
    PORT = config["PORT"]
    OPENAI_API_KEY = config["OPENAI_API_KEY"]
    OPENAI_CHAT_MODEL = config["OPENAI_CHAT_MODEL"]
    OPENAI_EMBED_MODEL = config["OPENAI_EMBED_MODEL"]
    PINECONE_API_KEY = config["PINECONE_API_KEY"]
    SERP_API_KEY = config["SERP_API_KEY"]
    MONGO_URL = config['MONGO_URL']
    os.environ['HTTP_PROXY'] = config['HTTP_PROXY']
    os.environ['HTTPS_PROXY'] = config['HTTPS_PROXY']
print("================")
print(f'PORT={PORT}')
print(f'OPENAI_API_KEY={OPENAI_API_KEY}')
print(f'OPENAI_CHAT_MODEL={OPENAI_CHAT_MODEL}')
print(f'OPENAI_EMBED_MODEL={OPENAI_EMBED_MODEL}')
print(f'PINECONE_API_KEY={PINECONE_API_KEY}')
print(f'SERP_API_KEY={SERP_API_KEY}')
print(f'MONGO_URL={MONGO_URL}')
print("================")

# 创建Flask应用
app = Flask(__name__)
# 密钥用来加密session到浏览器的cookie中
# 等价于 app.secret_key = "who dares win"
app.config['SECRET_KEY'] = "who dares win"
# session保存时长为1天
# 等价于app.permanent_session_lifetime = datetime.timedelta(days=1) 
app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(days=1)
# 限制上传文件不超过16MB
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  

# 创建OpenAI模型：chat模型和embedding模型
openai = kqa.OpenAI(OPENAI_API_KEY, OPENAI_CHAT_MODEL, OPENAI_EMBED_MODEL)
# 创建MongoDB数据库
mongo = kqa.MongoDB(MONGO_URL)
# 创建Pinecone向量数据库
pinecone = kqa.Pinecone(PINECONE_API_KEY)
# 创建Google搜索引擎
google = kqa.Google(SERP_API_KEY)
# 创建Chroma向量数据库
chroma = kqa.Chroma(OPENAI_API_KEY, OPENAI_EMBED_MODEL)

# 获取当前会话的状态
# state in ['register', 'login', 'prompt', 'chat']
def get_current_state():    
    # 登录阶段：没有登录，要先登录。
    state = 'login' 
    if ('name' in session) and ('uid' in session) and \
        mongo.user_exist(name=session['name'], uid=session['uid']):
        # 提示阶段：已经登录，没有提示。
        state = 'prompt' 
        if ("prompt" in session) and ("messages" in session) \
                                    and ("contexts" in session):
            # 交互阶段：已有提示，进入问答。
            state = 'chat' 
    return state

@app.route('/') # 默认methods=['GET']
def index():
    state = get_current_state()
    print(f"主页: 状态={state}")
    return render_template('index.html', state=state)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('index.html', state='register')
        
    assert(request.method == 'POST')
    # 用户信息
    name = request.form.get('name')
    pwd = request.form.get('pwd')
    pwd2 = request.form.get('pwd2')
    # 验证输入
    if (not pwd) or (not pwd2) or (pwd != pwd2):
        return render_template('index.html', \
                    state='register', auth_msg="密码错误")
    if (not name) or (not re.match("^[A-Za-z]+[A-Za-z0-9_]*$", name)):
        return render_template('index.html', \
                    state='register', auth_msg="名称错误")       
    # 新增用户
    uid = mongo.insert_user(name=name, pwd=pwd) 
    if not uid:
        return render_template('index.html', \
                    state='register', auth_msg="账号存在") 
    print(f"注册: 姓名={name}, uid={uid}")
    return render_template('index.html', \
                    state='login', auth_msg="注册成功")

@app.route('/login', methods=['POST'])
def login():
    # 用户信息
    name = request.form.get('name')
    pwd = request.form.get('pwd')
    # 验证输入
    if (not pwd) or (not name) \
            or (not re.match("^[A-Za-z]+[A-Za-z0-9_]*$", name)):
        return render_template('index.html', \
                    state='login', auth_msg="输入错误")
    # 查询记录
    if not mongo.validate_user(name, pwd):
        return render_template('index.html', \
                    state='login', auth_msg="登录失败")
    # 写入会话：相当于登录成功。
    user = mongo.find_user(name)
    session['name'] = user['name']
    session['uid'] = user['uid']
    if 'prompt' in user and user['prompt'] != "":
        session['prompt'] = user['prompt']
        session['messages'] = [{"role": "system", "content": user['prompt']}]
        session['contexts'] = []
        session['chattype'] = 'direct'
    titles = [f['title'] for f in mongo.find_files_by_user(name)]
    session['titles'] = titles
    print(f"登录: 姓名={name}, uid={session['uid']}")
    return redirect(url_for('index'))

@app.route('/logout', methods=['POST'])
def logout():
    name = session.get('name')
    uid = session.get('uid')
    if name and uid:
        print(f"登出: 姓名={name}, uid={uid}")
    # 清空会话：相当于登出成功。
    ## 不能用del session['xxx']，不存在时会异常。
    #session.pop('name', None) 
    #session.pop('uid', None)
    #session.pop('prompt', None)
    #session.pop('messages', None)
    #session.pop('contexts', None)
    #session.pop('chattype', None)
    session.clear()
    return redirect(url_for('index'))    

@app.route('/fetch', methods=['POST'])
def fetch():
    submit = request.form.get('submit')
    if submit == '上传文件':
        if 'file' not in request.files:
            return redirect(url_for('index'))
        file = request.files['file']
        # 从文件中提取title和paragraphs
        filename, filetype = os.path.splitext(file.filename)
        title = filename.strip()
        if not title:
            return render_template('index.html', \
                        state=get_current_state(), file_msg="文件没有标题")
        filetype = filetype.lower()
        if filetype not in ['.txt', '.pdf', '.doc', '.docx']:
            return redirect(url_for('index'))    # 永不进入：前端做了限制。
        filepath = "./tmp" # 保存到临时文件中
        file.save(filepath)
        if filetype == '.txt':
            with open(filepath, "r") as fp:
                paragraphs = fp.readlines()
        elif filetype == '.pdf':
            paragraphs = []
            with fitz.open(filepath) as doc:
                for page in doc:
                    text = page.get_text()
                    paragraphs.extend(text.split('\n'))
        elif filetype == '.doc':
            paragraphs = partition_doc(filename=filepath)
        elif filetype == '.docx':
            paragraphs = partition_docx(filename=filepath)
        paragraphs = [str(p).strip() for p in paragraphs \
                                      if str(p).strip() != ""]
        if not paragraphs:    
            return render_template('index.html', \
                        state=get_current_state(), file_msg="文件没有内容")
    elif submit == '抓取网页':        
        # 抓取网页：获取title和paragraphs
        url = request.form.get('url')
        okey, data = crawl_webpage(url=url)
        if not okey:
            file_msg = data
            return render_template('index.html', \
                        state=get_current_state(), file_msg=file_msg)
        title, paragraphs = data
    else:    
        return redirect(url_for('index'))    # 永不进入
    name = session['name']
    titles = session['titles']
    # 同名文件：要先删除文件和嵌入
    if title in titles:
        file_doc = mongo.find_file(name=name, title=title)
        if file_doc: # file_doc等价于title in titles
            file_id = file_doc['fid']
            num_chunks = len(file_doc['chunks'])
            # 删除文件
            mongo.delete_file(name=name, title=title)
            # 删除嵌入
            pinecone.delete(file_id=file_id, num_embeddings=num_chunks, \
                                                            namespace=name)
            # 删除标题
            titles.pop(titles.index(title))
    # 嵌入文件
    chunks, embeddings = openai.embed_document(paragraphs)
    # 新增文件
    file_id = mongo.insert_file(name=name, title=title, \
                         paragraphs=paragraphs, chunks=chunks)
    if file_id == None:
        return render_template('index.html', state=get_current_state(), \
                                                   file_msg="插入文件失败")
    # 新增嵌入
    pinecone.insert(file_id, embeddings, namespace=name)
    # 更新会话
    titles.append(title)
    session['titles'] = titles
    print(f"获取: 标题={title} 段落数={len(paragraphs)}, 节选数={len(chunks)}")
    return redirect(url_for('index'))

@app.route('/delete', methods=['POST'])
def delete():
    title_idx = request.form.get('title_idx')
    title_idx = int(title_idx)
    if 'titles' not in session or 'name' not in session:
        return redirect(url_for('index'))
    # 删除文件和嵌入
    title = session['titles'][title_idx]
    file_doc = mongo.find_file(name=session['name'], title=title)
    if not file_doc:
        return redirect(url_for('index'))
    file_id = file_doc['fid']
    num_chunks = len(file_doc['chunks'])
    # 删除文件
    mongo.delete_file(name=session['name'], title=title)
    # 删除嵌入
    pinecone.delete(file_id=file_id, num_embeddings=num_chunks, \
                    namespace=session['name'])
    # 更新会话
    titles = session['titles']
    titles.pop(title_idx)
    session['titles'] = titles
    print(f"删文: 标题={title}")
    return redirect(url_for('index'))

@app.route('/read', methods=['GET'])
def read():
    title_id = request.args.get('tid')
    file_id = request.args.get('fid')
    chunk_id = request.args.get('cid')
    if not title_id and not file_id:
        return redirect(url_for('index'))
    # 查找文件    
    if title_id:
        title_id = int(title_id)
        if 'titles' not in session or 'name' not in session:
            return redirect(url_for('index'))
        
        title = session['titles'][title_id]
        file_doc = mongo.find_file(name=session['name'], title=title)
        if not file_doc:
            return redirect(url_for('index'))            
        paragraphs = file_doc['paragraphs']
        chunks = file_doc['chunks']
    elif file_id:
        if 'name' not in session:
            return redirect(url_for('index'))
        file_doc = mongo.find_file(name=session['name'], file_id=file_id)
        if not file_doc:
            return redirect(url_for('index')) 
        title = file_doc['title']           
        paragraphs = file_doc['paragraphs']
        chunks = file_doc['chunks']
    if chunk_id != None:
        chunk_id = int(chunk_id)  # chunk_id为None或整数
    print(f"读文: 标题={title} 段落数={len(paragraphs)} 节选数={len(chunks)}")
    return render_template('read.html', title=title, \
                    paragraphs=paragraphs, chunks=chunks, chunk_id=chunk_id)

@app.route('/prompt', methods=['POST'])
def prompt():
    submit = request.form.get('submit')
    if submit == '重来':
        # 清空问答会话
        print(f"重来: 提示={session.get('prompt')}")
        session.pop('prompt', None)
        session.pop('messages', None)
        session.pop('contexts', None)
        session.pop('chattype', None)
        # 更新数据库
        mongo.update_user(name=session['name'], uid=session['uid'], prompt="")
        return redirect(url_for('index'))
    # submit == '提交'
    # 写入问答会话
    prompt = request.form.get('prompt')
    if prompt == "":
        return render_template('index.html', \
                    state='prompt', prompt_msg="提示不能为空")
    messages = [{"role": "system", "content": prompt}]
    contexts = []
    chattype = 'direct'
    session['prompt'] = prompt
    session['messages'] = messages
    session['contexts'] = contexts
    session['chattype'] = chattype
    # 保存prompt到数据库中
    mongo.update_user(name=session['name'], uid=session['uid'], \
                   prompt=session["prompt"])
    print(f"提示：提示={prompt}")
    return redirect(url_for('index'))

def search_context(query, query_embedding):
    # 搜索网页
    webpages = google.search(query=query)
    if not webpages:
        return None
    # webpages != None
    # 遍历网页
    for webpage in webpages:
        # 抓取网页
        url = webpage['link']
        okey, data = crawl_webpage(url=url)
        if not okey:
            continue
        title, paragraphs = data
        if len(paragraphs) == 0:
            continue
        title = webpage['title'] # 优先使用Google的title而非自动提取的title
        # 嵌入网页
        chunks, embeddings = openai.embed_document(paragraphs)
        # 新增嵌入
        chroma.insert(chunks=chunks, embeddings=embeddings, \
                      title=title, link=url)
    # 查询嵌入
    results = chroma.query(query_embedding=query_embedding, n_results=3)
    # 删除嵌入
    chroma.clear()
    if not results or len(results) == 0:
        return None
    # len(results) > 0
    documents = results['documents'][0]
    embeddings = results['embeddings'][0]
    #titles = [md['title'] for md in results['metadatas'][0]]
    links = [md['link'] for md in results['metadatas'][0]]
    #distances = results['distances'][0]
    return (documents, embeddings, links)
        
@app.route('/chat', methods=['POST'])
def chat():
    submit = request.form.get('submit')
    if submit == '删除':
        # message_idx对应用户发问的messages索引
        message_idx = int(request.form.get('message_idx'))
        if not('messages' in session and 'contexts' in session 
               and message_idx % 2 == 1):  # message_idx应为奇数
            return redirect(url_for('index'))
        # 删除>=message_idx的所有问答和上下文
        messages = session['messages']
        contexts = session['contexts']
        messages = messages[:message_idx]
        contexts = contexts[:int((message_idx-1)/2)]
        session['messages'] = messages
        session['contexts'] = contexts
        print(f"删话: 消息数={len(messages)}, 上下文数={len(contexts)}")
        return redirect(url_for('index'))
    # submit == '发送'
    # 获取问题: question
    question = request.form.get('question')
    if not (question and 'messages' in session and 'contexts' in session):
        return redirect(url_for('index'))
    # 获取上下文：context
    chattype = request.form.get('chattype')  # or session['chattype']
    context = []   # 单个context里面有多个chunks
    if chattype == 'document':
        # 检索文档
        name = session['name']
        question_embedding = openai.embed_query(query=question)
        result = pinecone.query(query_embedding=question_embedding, \
                                                 namespace=name, top_k=3)
        if result:  # 最相关的文档嵌入存在
            scores, ids = result
            for i, score in enumerate(scores):
                if score > 0.8: # 相关度要大于0.8
                    file_id, chunk_id = ids[i]
                    file_doc = mongo.find_file(name=name, file_id=file_id)
                    if file_doc:  # 相应的文件存在
                        chunk = file_doc['chunks'][chunk_id]
                        link = url_for('read', fid=file_id, cid=chunk_id)
                        context.append({'link':link, 'chunk':chunk})
    elif chattype == 'search':
        # 搜索网页
        question_embedding = openai.embed_query(query=question)
        result = search_context(question, question_embedding)
        if result:  # 最相关的网页嵌入存在
            documents, embeddings, links = result
            for i, embedding in enumerate(embeddings):
                score = np.array(question_embedding).dot(np.array(embedding))
                if score > 0.8:  # 相关度要大与0.8
                    document = documents[i]
                    link = links[i]
                    context.append({'link':link, 'chunk':document})
    else:  # chattype == 'direct'
        pass  # context == []
    # 问答服务：answer
    messages = session['messages']
    contexts = session['contexts']
    if context == []:
        # 直接问答
        messages.append({"role":"user", "content":question})
        okey, result = openai.answer_question(messages)
    else:  # context != []   
        # 间接问答
        if len(context) == 1:
            contexted_question = "根据以下内容回答问题：\n内容：" \
                        + context[0]['chunk'] + "\n问题：" + question
        elif len(context) == 2:
            contexted_question = "根据以下两个文章节选回答问题：\n节选一：" \
                                + context[0]['chunk'] + "\n节选二：" \
                                + context[1]['chunk'] + "\n问题：" + question
        elif len(context) == 3:
            contexted_question = "根据以下三个文章节选回答问题：\n节选一：" \
                                + context[0]['chunk'] + "\n节选二：" \
                                + context[1]['chunk'] + "\n节选三：" \
                                + context[2]['chunk'] + "\n问题：" + question
        else:
            return redirect(url_for('index'))  # 永不进入
        messages.append({"role":"user", "content":contexted_question})
        okey, result = openai.answer_question(messages)
        messages.pop(-1)
        messages.append({"role":"user", "content":question})
    if not okey:
        err_msg = result
        return render_template('index.html', \
                               state='chat', chat_msg=err_msg)
    answer = result
    # 更新会话
    messages.append({"role":"assistant", "content":answer})
    session['messages']=messages
    contexts.append(context)
    session['contexts']=contexts
    session['chattype'] = chattype
    print(f"交谈: \n[问题]{question}\n[上下文]{context}\n[答案]{answer}")
    return redirect(url_for('index'))
 
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=True)
    
    