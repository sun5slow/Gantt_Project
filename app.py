import os
from flask import Flask, render_template, jsonify, request, Blueprint
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate, upgrade
from datetime import date, datetime, timedelta

# ----------------- APP INITIALIZATION -----------------
app = Flask(__name__)

# ----------------- CONFIGURATIONS -----------------
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'gantt.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ----------------- DATABASE SETUP -----------------
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ----------------- API BLUEPRINT -----------------
# Blueprint 'api'를 먼저 정의해야 합니다.
api = Blueprint('api', __name__, url_prefix='/api')

# ----------------- DATABASE MODELS -----------------
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=True)
    name = db.Column(db.String(255), nullable=False)
    start = db.Column(db.Date, nullable=False)
    end = db.Column(db.Date, nullable=False)
    progress = db.Column(db.Integer, default=0)
    assignee = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(50), nullable=True)
    task_type = db.Column(db.String(50), default='task', nullable=False)

class Link(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.Integer, db.ForeignKey('task.id'))
    target = db.Column(db.Integer, db.ForeignKey('task.id'))
    type = db.Column(db.String(1))

class Baseline(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class BaselineTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    baseline_id = db.Column(db.Integer, db.ForeignKey('baseline.id'), nullable=False)
    original_task_id = db.Column(db.Integer, nullable=False)
    start = db.Column(db.Date, nullable=False)
    end = db.Column(db.Date, nullable=False)

# ----------------- HELPER FUNCTIONS -----------------
def str_to_date(date_str):
    return datetime.strptime(date_str, '%Y-%m-%d').date()

def date_to_str(date_obj):
    return date_obj.strftime('%Y-%m-%d')

# ----------------- API ROUTES -----------------
# 'api'가 정의된 후에 라우트를 설정합니다.
@api.route("/data", methods=["GET"])
def get_data():
    tasks_from_db = Task.query.order_by(Task.id).all()
    links_from_db = Link.query.all()
    
    tasks_map = {task.id: task for task in tasks_from_db}
    children_map = {}
    for task in tasks_from_db:
        if task.parent_id:
            if task.parent_id not in children_map:
                children_map[task.parent_id] = []
            children_map[task.parent_id].append(task.id)

    tasks_data = []
    for task in tasks_from_db:
        start_date_to_use = task.start
        end_date_to_use = task.end
        progress = task.progress / 100
        task_type_to_use = task.task_type

        if task.task_type == 'project':
            if task.id not in children_map:
                task_type_to_use = 'task'
            else:
                child_tasks = [tasks_map[child_id] for child_id in children_map[task.id] if child_id in tasks_map]
                if child_tasks:
                    start_date_to_use = min(ct.start for ct in child_tasks)
                    end_date_to_use = max(ct.end for ct in child_tasks)
                
                child_tasks_with_duration = [ct for ct in child_tasks if (ct.end - ct.start).days > 0]
                if child_tasks_with_duration:
                    total_duration = sum([(ct.end - ct.start).days for ct in child_tasks_with_duration])
                    if total_duration > 0:
                        weighted_progress_sum = sum([(ct.end - ct.start).days * ct.progress for ct in child_tasks_with_duration])
                        progress = (weighted_progress_sum / total_duration) / 100
        
        duration = (end_date_to_use - start_date_to_use).days

        tasks_data.append({
            'id': task.id, 
            'text': task.name, 
            'start_date': date_to_str(start_date_to_use),
            'end_date': date_to_str(end_date_to_use), 
            'duration': duration, 
            'progress': round(progress, 2),
            'parent': task.parent_id or 0, 
            'type': task_type_to_use,
            'assignee': task.assignee, 
            'status': task.status
        })
        
    links_data = [{'id': link.id, 'source': link.source, 'target': link.target, 'type': link.type} for link in links_from_db]
    
    baseline_id = request.args.get('baseline_id')
    if baseline_id:
        baseline_tasks = BaselineTask.query.filter_by(baseline_id=baseline_id).all()
        for bl_task in baseline_tasks:
            for task_data in tasks_data:
                if task_data['id'] == bl_task.original_task_id:
                    task_data['planned_start'] = date_to_str(bl_task.start)
                    task_data['planned_end'] = date_to_str(bl_task.end)
                    break

    return jsonify({"data": tasks_data, "links": links_data})

@api.route("/task", methods=["POST"])
def add_task():
    data = request.form
    new_task = Task(
        name=data.get('text'), start=str_to_date(data.get('start_date')),
        end=str_to_date(data.get('end_date')), progress=int(float(data.get('progress', 0)) * 100),
        parent_id=data.get('parent') if data.get('parent') != '0' else None,
        task_type=data.get('type', 'task'), assignee=data.get('assignee', ''), status=data.get('status', '대기')
    )
    db.session.add(new_task)
    db.session.commit()
    return jsonify({"action": "inserted", "tid": new_task.id})

@api.route("/task/<int:id>", methods=["PUT"])
def update_task(id):
    data = request.form
    task = db.session.get(Task, id)
    if not task: return jsonify({"action": "error"})
    
    task.name = data.get('text', task.name)
    task.start = str_to_date(data.get('start_date', date_to_str(task.start)))
    task.end = str_to_date(data.get('end_date', date_to_str(task.end)))
    if data.get('type') != 'project':
      task.progress = int(float(data.get('progress', task.progress / 100)) * 100)
    task.parent_id = data.get('parent') if data.get('parent') != '0' else None
    task.task_type = data.get('type', task.task_type)
    task.assignee = data.get('assignee', task.assignee)
    task.status = data.get('status', task.status)
    db.session.commit()
    return jsonify({"action": "updated"})

@api.route("/task/<int:id>", methods=["DELETE"])
def delete_task(id):
    task = db.session.get(Task, id)
    if not task: return jsonify({"action": "error"})
    db.session.delete(task)
    db.session.commit()
    return jsonify({"action": "deleted"})

@api.route("/link", methods=["POST"])
def add_link():
    data = request.form
    new_link = Link(id=data.get("id"), source=data.get('source'), target=data.get('target'), type=data.get('type'))
    db.session.add(new_link)
    db.session.commit()
    return jsonify({"action": "inserted", "tid": new_link.id})

@api.route("/link/<int:id>", methods=["PUT"])
def update_link(id):
    data = request.form
    link = db.session.get(Link, id)
    if not link: return jsonify({"action": "error"})
    link.source = data.get('source', link.source)
    link.target = data.get('target', link.target)
    link.type = data.get('type', link.type)
    db.session.commit()
    return jsonify({"action": "updated"})

@api.route("/link/<int:id>", methods=["DELETE"])
def delete_link(id):
    link = db.session.get(Link, id)
    if not link: return jsonify({"action": "error"})
    db.session.delete(link)
    db.session.commit()
    return jsonify({"action": "deleted"})

@api.route('/baselines', methods=['POST'])
def create_baseline():
    name = request.json.get('name', f"Baseline {date.today().isoformat()}")
    new_baseline = Baseline(name=name)
    db.session.add(new_baseline)
    db.session.flush()
    tasks = Task.query.all()
    for task in tasks:
        db.session.add(BaselineTask(baseline_id=new_baseline.id, original_task_id=task.id, start=task.start, end=task.end))
    db.session.commit()
    return jsonify({'message': 'Baseline created successfully!', 'baseline': {'id': new_baseline.id, 'name': new_baseline.name}})

@api.route('/baselines', methods=['GET'])
def get_baselines():
    baselines = Baseline.query.order_by(Baseline.created_at.desc()).all()
    return jsonify([{'id': b.id, 'name': b.name} for b in baselines])

# Blueprint를 app에 등록
app.register_blueprint(api)

# ----------------- OTHER ROUTES -----------------
@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/')
def index():
    return render_template('index.html')
    
# ----------------- CLI COMMANDS -----------------
@app.cli.command("seed-db")
def seed_db():
    # 데이터베이스 테이블 존재 여부 확인 후 없으면 생성
    with app.app_context():
        db.create_all()

    if Task.query.first():
        print("Database already seeded.")
        return
        
    sample_tasks = [
        Task(id=1, name='요약 작업 1', start=date(2025, 9, 19), end=date(2025, 9, 27), progress=0, assignee='홍길동', status='진행 중', task_type='project'),
        Task(id=2, parent_id=1, name='하위 작업 1-1', start=date(2025, 9, 19), end=date(2025, 9, 23), progress=50, assignee='이순신', status='진행 중'),
        Task(id=3, parent_id=1, name='하위 작업 1-2', start=date(2025, 9, 23), end=date(2025, 9, 27), progress=20, assignee='홍길동', status='대기'),
        Task(id=4, name='중간 보고', start=date(2025, 9, 27), end=date(2025, 9, 27), progress=100, task_type='milestone'),
        Task(id=5, name='독립 작업 2', start=date(2025, 9, 28), end=date(2025, 10, 1), progress=0, assignee='강감찬', status='대기')
    ]
    sample_links = [ Link(id=1, source=3, target=4, type='0') ]
    db.session.bulk_save_objects(sample_tasks)
    db.session.bulk_save_objects(sample_links)
    db.session.commit()
    print("Database has been seeded with sample data.")

# ----------------- APP RUN -----------------
if __name__ == '__main__':
    app.run(debug=True, port=5001)