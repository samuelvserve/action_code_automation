"""Portal navigation, dashboard drill-down and folder schedule integration."""
import hashlib
import json
import os
from datetime import datetime
from unittest.mock import patch

from database import db
from me2m_pipeline import REPORT_TIMEZONE
from services import me2m_runner, scheduler
from services.dashboard_stats import chart_data
from test_me2m import app_env, upload_and_wait, workbook_bytes


def test_login_requires_portal_confirmation_and_selection_persists(app_env):
    app, client, buyer_id, _ = app_env
    client.get("/logout")
    response = client.post("/login?next=//example.org", data={"username": "buyer", "password": "test-password"})
    assert response.location.endswith("/portals")
    with client.session_transaction() as session:
        assert session["user_id"] == buyer_id
        assert "active_portal" not in session
    assert client.get("/dashboard?process=me2m").location.endswith("/portals")
    popup = client.get("/portals")
    assert b'portal-choice-dialog' in popup.data and b'Action Code' in popup.data and b'ME2M' in popup.data
    assert client.post("/portals", data={"portal": "other"}).status_code == 200
    response = client.post("/portals", data={"portal": "me2m"})
    assert "process=me2m" in response.location
    dashboard = client.get("/dashboard")
    assert b"ME2M overview" in dashboard.data
    assert b'href="/me2m/schedule"' in dashboard.data
    assert b'href="/schedule"' not in dashboard.data
    client.post("/portals", data={"portal": "acr"})
    dashboard = client.get("/dashboard")
    assert b"Action Code overview" in dashboard.data and b'href="/schedule"' in dashboard.data
    assert b'href="/me2m/schedule"' not in dashboard.data


def test_dashboards_show_charts_and_files_with_breakdowns_only_in_details(app_env):
    _, client, _, _ = app_env
    run = upload_and_wait(client)
    dashboard = client.get("/dashboard?process=me2m&period=0")
    assert b"Processing activity" in dashboard.data and b"Run outcomes" in dashboard.data
    assert b'Period breakdown' not in dashboard.data
    assert b'By purchasing group' not in dashboard.data
    assert run["output_file"].encode() in dashboard.data
    assert f'href="/me2m/{run["id"]}"'.encode() in dashboard.data
    detail = client.get(f'/me2m/{run["id"]}')
    assert b'Period breakdown' in detail.data and b'By purchasing group' in detail.data
    assert b'Period rules' not in client.get('/me2m').data
    assert b'minus 7 days' not in client.get('/me2m').data
    assert run["output_file"].encode() in client.get('/me2m/history').data
    assert run["output_file"].encode() not in client.get('/me2m/history?q=no-match').data
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rid = db.execute('INSERT INTO runs (run_type,purchasing_codes,status,started_at,total_rows,action_items,output_file,code_stats) VALUES (?,?,?,?,?,?,?,?)',
                     ('Daily', 'D13,D40', 'success', now, 100, 30, 'Action_Run.xlsx', json.dumps({'D13': {'rows': 70, 'action_items': 20}, 'D40': {'rows': 30, 'action_items': 10}})))
    action = client.get('/dashboard?process=acr&period=0')
    assert b'Processing activity' in action.data and b'Action_Run.xlsx' in action.data
    assert b'By purchasing code' not in action.data
    assert b'By purchasing code' in client.get(f'/run/{rid}').data
    scoped = chart_data(db.query('SELECT * FROM runs'), 7, 'acr', codes=['D13'])
    assert sum(p['rows'] for p in scoped['series']) == 70
    assert sum(p['value'] for p in scoped['series']) == 20


def test_me2m_schedule_reads_newest_source_and_runs_once_without_acr_or_email(app_env, tmp_path):
    _, client, buyer_id, _ = app_env
    folder = tmp_path / 'input-folder'
    folder.mkdir()
    old = folder / 'old.xlsx'
    latest = folder / 'new.xlsx'
    old.write_bytes(workbook_bytes())
    latest.write_bytes(workbook_bytes())
    ignored = folder / 'new_Processed.xlsx'
    ignored.write_bytes(b'not a source workbook')
    (folder / '~$open.xlsx').write_bytes(b'locked workbook')
    os.utime(old, (1000, 1000))
    os.utime(latest, (2000, 2000))
    os.utime(ignored, (3000, 3000))
    fingerprint = hashlib.sha256(latest.read_bytes()).hexdigest()
    with client.session_transaction() as session:
        session.update(user_id=buyer_id, username='buyer', role='buyer', active_portal='me2m')
    response = client.post('/me2m/schedule', data={'name': 'Daily ME2M', 'input_folder': str(folder), 'run_time': '07:00', 'enabled': 'on', 'days': ['4']})
    assert response.status_code == 302
    schedule = db.query_one('SELECT * FROM me2m_schedules')
    assert schedule['created_by'] == 'buyer'
    assert not db.query('SELECT * FROM schedules')
    now = datetime(2026, 10, 9, 7, 1, tzinfo=REPORT_TIMEZONE)
    with patch('services.scheduler.start_run') as acr, patch('services.mailer.notify_run') as mailer:
        scheduler.check_me2m_schedules(now)
        scheduler.check_me2m_schedules(now)
        run = db.query_one('SELECT * FROM me2m_runs')
        worker = me2m_runner._RUNS.get(run['id'])
        if worker:
            worker[0].join(15)
            assert not worker[0].is_alive()
        acr.assert_not_called()
        mailer.assert_not_called()
    assert len(db.query('SELECT * FROM me2m_runs')) == 1
    run = db.query_one('SELECT * FROM me2m_runs')
    assert run['status'] == 'success'
    assert run['source_file'] == 'new.xlsx' and run['report_date'] == '2026-10-09'
    assert run['triggered_by'] == 'buyer' and run['schedule_id'] == schedule['id']
    assert run['total_rows'] == 1 and run['net_order_value'] == 1200.5
    assert hashlib.sha256(latest.read_bytes()).hexdigest() == fingerprint
    assert not list(me2m_runner.ME2M_UPLOAD_DIR.iterdir())
    saved = db.query_one('SELECT * FROM me2m_schedules')
    assert saved['last_run_id'] == run['id'] and saved['last_run_date'] == '2026-10-09'
    assert b'View run #' in client.get('/me2m/schedule').data
    client.post(f'/me2m/schedule/{schedule["id"]}/toggle')
    assert db.query_one('SELECT enabled FROM me2m_schedules')['enabled'] == 0


def test_schedule_errors_are_visible_and_pause_weekdays_and_ownership_are_enforced(app_env, tmp_path):
    _, client, buyer_id, other_id = app_env
    folder = tmp_path / 'empty-inputs'
    folder.mkdir()
    with client.session_transaction() as session:
        session.update(user_id=buyer_id, username='buyer', role='buyer', active_portal='me2m')
    assert client.post('/me2m/schedule', data={'run_time': '25:00', 'input_folder': str(folder)}).status_code == 200
    assert not db.query('SELECT * FROM me2m_schedules')
    client.post('/me2m/schedule', data={'name': 'Folder schedule', 'run_time': '07:00', 'input_folder': str(folder), 'enabled': 'on', 'days': ['4']})
    schedule = db.query_one('SELECT * FROM me2m_schedules')
    scheduler.check_me2m_schedules(datetime(2026, 10, 8, 8, tzinfo=REPORT_TIMEZONE))
    scheduler.check_me2m_schedules(datetime(2026, 10, 9, 6, tzinfo=REPORT_TIMEZONE))
    assert db.query_one('SELECT last_run_date FROM me2m_schedules')['last_run_date'] is None
    scheduler.check_me2m_schedules(datetime(2026, 10, 9, 8, tzinfo=REPORT_TIMEZONE))
    error = db.query_one('SELECT last_error FROM me2m_schedules')['last_error']
    assert 'No source .xlsx' in error
    assert error.encode() in client.get('/me2m/schedule').data
    scheduler.check_me2m_schedules(datetime(2026, 10, 9, 9, tzinfo=REPORT_TIMEZONE))
    assert not db.query('SELECT * FROM me2m_runs')
    with client.session_transaction() as session:
        session.update(user_id=other_id, username='other', role='buyer')
    assert b'Folder schedule' not in client.get('/me2m/schedule').data
    assert client.post(f'/me2m/schedule/{schedule["id"]}/toggle').status_code == 403
    assert client.post(f'/me2m/schedule/{schedule["id"]}/delete').status_code == 403
    with client.session_transaction() as session:
        session.update(user_id=buyer_id, username='buyer', role='buyer')
    client.post(f'/me2m/schedule/{schedule["id"]}/delete')
    assert not db.query('SELECT * FROM me2m_schedules')


def test_disabled_and_active_previous_reports_do_not_dispatch(app_env, tmp_path):
    _, client, _, _ = app_env
    folder = tmp_path / 'input-folder'
    folder.mkdir()
    (folder / 'source.xlsx').write_bytes(workbook_bytes())
    client.post('/me2m/schedule', data={'run_time': '07:00', 'input_folder': str(folder)})
    now = datetime(2026, 10, 9, 8, tzinfo=REPORT_TIMEZONE)
    assert scheduler.next_run(db.query_one('SELECT * FROM me2m_schedules'), now) is None
    scheduler.check_me2m_schedules(now)
    assert not db.query('SELECT * FROM me2m_runs')
    running_id = db.execute('INSERT INTO me2m_runs (source_file,report_date,started_at,triggered_by) VALUES (?,?,?,?)',
                            ('source.xlsx', '2026-10-08', '2026-10-08 07:00:00', 'admin'))
    db.execute('UPDATE me2m_schedules SET enabled=1,last_run_id=?', (running_id,))
    with patch.object(me2m_runner, 'start_run') as starter:
        scheduler.check_me2m_schedules(now)
        starter.assert_not_called()
