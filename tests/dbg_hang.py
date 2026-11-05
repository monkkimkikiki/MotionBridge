import subprocess, sys, json, time, threading, os
env = dict(os.environ, ANIMREF_WORKER_TRACE="25", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
p = subprocess.Popen([sys.executable, '-X', 'utf8', os.path.abspath('motionbridge/animref/worker.py')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd='motionbridge')
p.stdin.write((json.dumps({'fn': '없는작업', 'params': {}, 'settings': {}}, ensure_ascii=False) + '\n').encode('utf-8')); p.stdin.flush()
err = []
threading.Thread(target=lambda: [err.append(l) for l in iter(p.stderr.readline, b'')], daemon=True).start()
out = []
threading.Thread(target=lambda: [out.append(l) for l in iter(p.stdout.readline, b'')], daemon=True).start()
t = time.time()
while time.time() - t < 45 and p.poll() is None:
    time.sleep(0.5)
print('elapsed', time.time() - t, 'rc', p.poll())
print('STDOUT', b''.join(out).decode('utf-8', 'replace'))
print('STDERR', b''.join(err).decode('utf-8', 'replace'))
p.kill()
