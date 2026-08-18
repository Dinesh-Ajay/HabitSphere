const STORAGE_KEY = 'habitsphere-web-data-v1';
let data = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{"theme":"light"}');
let insightDays = 7;

const $ = (s) => document.querySelector(s);
const save = () => localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
const dateLabel = new Intl.DateTimeFormat('en-US',{weekday:'long',month:'long',day:'numeric'}).format(new Date());
$('#todayLabel').textContent = dateLabel.toUpperCase();

function escapeHtml(value) { const d=document.createElement('div');d.textContent=value;return d.innerHTML; }
function toast(message) { const el=$('#toast');el.textContent=message;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),2600); }

// SPA navigation: every [data-view] / [data-view-target] click shows the matching section.
// This is the single source of truth for view switching (dashboard, habits, tracker, insights, reports, settings).
document.addEventListener('click', e => {
  const nav = e.target.closest('[data-view], [data-view-target]');
  if (!nav) return;
  const view = nav.dataset.view || nav.dataset.viewTarget;
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `${view}View`));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === view));
  $('#pageTitle').textContent = view === 'dashboard' ? 'Good morning, achiever.' : view === 'habits' ? 'Build the life you want.' : 'Learn from your progress.';
  window.scrollTo({ top: 0, behavior: 'smooth' });
});

$('#themeButton').onclick = () => { data.theme = data.theme === 'dark' ? 'light' : 'dark'; save(); applyTheme(); };
function applyTheme() { document.body.classList.toggle('dark', data.theme === 'dark'); $('#themeButton').textContent = data.theme === 'dark' ? '☾' : '☼'; }
applyTheme();

// Phase 2 authentication uses the Python JSON API and an HttpOnly session cookie.
async function authRequest(url, payload = null) {
  const options = { method: payload ? 'POST' : 'GET', credentials: 'same-origin', headers: {} };
  if (payload) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(payload); }
  const response = await fetch(url, options);
  const result = await response.json().catch(() => ({ success: false, message: 'Unexpected server response.' }));
  if (!response.ok) throw new Error(result.message || 'Request failed.');
  return result;
}
function showAuthFeedback(message = '', type = 'error') { const el = $('#authFeedback'); el.textContent = message; el.className = `auth-feedback ${message ? `visible ${type}` : ''}`; }
function setAuthMode(mode) { const registering = mode === 'register'; $('#loginForm').classList.toggle('hidden', registering); $('#registerForm').classList.toggle('hidden', !registering); document.querySelectorAll('[data-auth-mode]').forEach(button => button.classList.toggle('active', button.dataset.authMode === mode)); showAuthFeedback(); }
function signInUser(user) { document.body.classList.add('authenticated'); $('#profileName').textContent = user.full_name; $('#profileEmail').textContent = user.email; $('#profileInitial').textContent = user.full_name.charAt(0).toUpperCase(); $('#pageTitle').textContent = `Welcome, ${user.full_name.split(' ')[0]}.`; loadDashboard(); loadHabits(); loadAnalytics(); loadSettings(true); }
async function loadDashboard() {
  try {
    const { dashboard } = await authRequest('/api/dashboard');
    $('#totalHabitsStat').textContent = dashboard.total_habits;
    $('#activeHabitsStat').textContent = dashboard.active_habits;
    $('#completedStat').textContent = dashboard.today_completed;
    $('#streakStat').textContent = `${dashboard.current_streak} day${dashboard.current_streak === 1 ? '' : 's'}`;
    $('#longestStreakStat').textContent = `${dashboard.longest_streak} day${dashboard.longest_streak === 1 ? '' : 's'}`;
    $('#overallPercentageStat').textContent = `${dashboard.overall_completion_percentage}%`;
    $('#dailyPercent').textContent = `${dashboard.today_completion_percentage}%`;
    $('#progressRing').style.setProperty('--progress', `${dashboard.today_completion_percentage}%`);
    $('#focusText').textContent = dashboard.active_habits ? 'Make today count.' : 'Your next habit starts here.';
    $('#focusDescription').textContent = dashboard.active_habits ? `${dashboard.today_completed} active habits completed today.` : 'Create a habit to begin tracking your progress.';
    $('#weeklyGoalText').textContent = `${dashboard.weekly_goal.completed} / ${dashboard.weekly_goal.target}`;
    $('#monthlyGoalText').textContent = `${dashboard.monthly_goal.completed} / ${dashboard.monthly_goal.target}`;
    $('#weeklyGoalBar').style.width = `${dashboard.weekly_goal.percentage}%`;
    $('#monthlyGoalBar').style.width = `${dashboard.monthly_goal.percentage}%`;
    renderDashboardWeek(dashboard.weekly_activity || [], dashboard.active_habits);
    await renderDashboardTodayHabits(dashboard.total_habits);
  } catch (error) { console.error('Dashboard data could not be loaded:', error); }
}
async function renderDashboardTodayHabits(totalHabits = 0) {
  const root = $('#todayHabits');
  try {
    const [trackerResult, analyticsResult] = await Promise.all([
      authRequest(`/api/tracker?date=${localDateValue()}`),
      authRequest('/api/analytics?period=30').catch(() => null),
    ]);
    const habits = trackerResult.tracker.habits;
    renderDashboardSuggestion(analyticsResult?.analytics);
    if (!habits.length) {
      // Only the "no habits at all" message when the whole account has zero habits;
      // otherwise the account simply has no *active* habits scheduled for today.
      root.innerHTML = totalHabits ? '<div class="empty-state">No active habits for today.</div>' : '<div class="empty-state">No habits yet. Add one and make today meaningful.</div>';
      return;
    }
    const streakByHabit = new Map((analyticsResult?.analytics?.habits || []).map(item => [item.habit_id, item.current_streak]));
    root.innerHTML = habits.map(habit => {
      const streakValue = streakByHabit.get(habit.habit_id);
      const streakLabel = streakValue ? ` · ♨ ${streakValue} day${streakValue === 1 ? '' : 's'}` : '';
      return `<div class="today-habit"><span class="habit-dot" style="--habit-color:${habit.completed ? '#45b99a' : '#a5a3b4'}"></span><div><strong>${escapeHtml(habit.habit_name)}</strong><small>${escapeHtml(habit.category)} · ${habit.completion_count || 0}/${habit.target_count} ${habit.goal_type}${streakLabel}</small></div><span class="streak ${habit.completed ? 'dashboard-completed' : 'dashboard-pending'}">${habit.completed ? '✓ Completed' : '○ Pending'}</span></div>`;
    }).join('');
  } catch (error) { root.innerHTML = '<div class="empty-state">Unable to load today’s habits.</div>'; }
}
// Rule-based nudge (no AI, per spec) driven by the same Analytics summary used on the Insights page.
function renderDashboardSuggestion(analytics) {
  const habits = analytics?.habits || [];
  if (!habits.length) return; // keep the default "Add your first habit…" placeholder
  const least = analytics.least_consistent;
  const most = analytics.most_consistent;
  const strongStreak = [...habits].sort((a, b) => b.current_streak - a.current_streak)[0];
  if (least && least.completion_percentage < 50) {
    $('#suggestionTitle').textContent = `Make "${least.habit_name}" easier to start`;
    $('#suggestionText').textContent = `It's at ${least.completion_percentage}% completion over the last 30 days. Try attaching it to a routine you already do, or lowering the target for now.`;
  } else if (strongStreak && strongStreak.current_streak >= 3) {
    $('#suggestionTitle').textContent = `"${strongStreak.habit_name}" is becoming automatic`;
    $('#suggestionText').textContent = `You're on a ${strongStreak.current_streak}-day streak. Protect it by deciding exactly when you'll do it tomorrow.`;
  } else if (most) {
    $('#suggestionTitle').textContent = 'Consistency grows from simple starts';
    $('#suggestionText').textContent = `${most.habit_name} is your strongest habit at ${most.completion_percentage}%. Keep it visible and build the next one the same way.`;
  }
}
function renderDashboardWeek(activity, activeHabits = 0) {
  const root = $('#weekChart');
  const maxValue = Math.max(...activity.map(day => day.completed), 1);
  root.innerHTML = activity.map(day => `<div class="day-bar"><span style="height:${day.completed === 0 ? 4 : Math.max(12, day.completed / maxValue * 100)}%" title="${day.completed} completed"></span><label>${day.day.charAt(0)}</label></div>`).join('');
  const values = activity.map(day => day.completed);
  const total = values.reduce((sum, value) => sum + value, 0);
  const daysActive = values.filter(value => value > 0).length;
  const earlierHalf = values.slice(0, 3).reduce((sum, value) => sum + value, 0);
  const laterHalf = values.slice(-3).reduce((sum, value) => sum + value, 0);
  const dailyAverage = activeHabits ? total / (values.length * activeHabits) : 0;
  let trend = 'Start today';
  if (total > 0) {
    if (dailyAverage >= 0.75 && daysActive >= values.length - 1) trend = '★ Excellent';
    else if (laterHalf > earlierHalf) trend = '↗ Improving';
    else if (daysActive >= Math.ceil(values.length / 2)) trend = '→ Consistent';
    else trend = '⚠ Needs attention';
  }
  $('#trendText').textContent = trend;
}
function signOutUser() { document.body.classList.remove('authenticated'); $('#loginForm').reset(); $('#loginPassword').value = ''; setAuthMode('login'); }
function initializeAuthentication() {
  $('#trackerDate').value = localDateValue();
  $('#settingsForm').addEventListener('submit', saveSettings);
  document.querySelector('[data-view="settings"]').addEventListener('click', () => loadSettings(false));
  $('#trackerDate').addEventListener('change', loadTracker);
  document.querySelector('[data-view="tracker"]').addEventListener('click', loadTracker);
  document.querySelector('[data-view="insights"]').addEventListener('click', loadAnalytics);
  document.querySelectorAll('[data-report]').forEach(button=>button.addEventListener('click',()=>generateReport(button.dataset.report)));
  $('#generateCharts').addEventListener('click', generateCharts);
  document.addEventListener('click',event=>{const button=event.target.closest('[data-track-save]');if(button)saveTrackerCompletion(button.dataset.trackSave);});
  setupHabitForm();
  $('#openModal').onclick = () => openHabitModal();
  $('#openModalSecondary').onclick = () => openHabitModal();
  $('#quickAddHabit').onclick = () => openHabitModal();
  $('#closeModal').onclick = () => $('#modalBackdrop').classList.remove('open');
  $('#modalBackdrop').onclick = event => { if (event.target === event.currentTarget) $('#modalBackdrop').classList.remove('open'); };
  ['habitSearch', 'categoryFilter', 'goalTypeFilter', 'statusFilter'].forEach(id => document.getElementById(id).addEventListener(id === 'habitSearch' ? 'input' : 'change', loadHabits));
  document.addEventListener('click', event => { const button = event.target.closest('[data-habit-view],[data-habit-edit],[data-habit-toggle],[data-habit-delete]'); if (!button) return; if (button.dataset.habitView) showHabit(button.dataset.habitView); if (button.dataset.habitEdit) editHabit(button.dataset.habitEdit); if (button.dataset.habitToggle) toggleHabit(button.dataset.habitToggle); if (button.dataset.habitDelete) deleteHabit(button.dataset.habitDelete); });
  document.querySelector('[data-view="habits"]').addEventListener('click', loadHabits);
  document.querySelectorAll('[data-auth-mode]').forEach(button => button.addEventListener('click', () => setAuthMode(button.dataset.authMode)));
  $('#registerForm').addEventListener('submit', async event => { event.preventDefault(); const button = event.submitter; button.disabled = true; try { const result = await authRequest('/api/auth/register', { full_name: $('#registerName').value, email: $('#registerEmail').value, password: $('#registerPassword').value }); $('#loginEmail').value = result.user.email; $('#registerForm').reset(); setAuthMode('login'); showAuthFeedback(result.message, 'success'); } catch (error) { showAuthFeedback(error.message); } finally { button.disabled = false; } });
  $('#loginForm').addEventListener('submit', async event => { event.preventDefault(); const button = event.submitter; button.disabled = true; try { const result = await authRequest('/api/auth/login', { email: $('#loginEmail').value, password: $('#loginPassword').value }); signInUser(result.user); toast('Welcome back, ' + result.user.full_name.split(' ')[0] + '!'); } catch (error) { showAuthFeedback(error.message); } finally { button.disabled = false; } });
  $('#logoutButton').addEventListener('click', async () => { try { await authRequest('/api/auth/logout', {}); } catch (error) { console.error(error); } finally { signOutUser(); } });
  $('#quickTrackToday').addEventListener('click', () => document.querySelector('[data-view="habits"]').click());
  $('#quickViewReports').addEventListener('click', () => document.querySelector('[data-view="reports"]').click());
  authRequest('/api/auth/me').then(result => signInUser(result.user)).catch(() => { signOutUser(); if (location.protocol === 'file:') showAuthFeedback('Start the site with python app.py, then open http://127.0.0.1:8000.', 'error'); });
}
window.addEventListener('load', initializeAuthentication);
let managedHabits = [];
async function habitRequest(url, method = 'GET', payload = null) {
  const options = { method, credentials: 'same-origin', headers: {} };
  if (payload) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(payload); }
  const response = await fetch(url, options);
  const result = await response.json().catch(() => ({ success: false, message: 'Unexpected server response.' }));
  if (!response.ok) throw new Error(result.message || 'Habit request failed.');
  return result;
}
function setupHabitForm() {
    const form = $('#habitForm');

    form.querySelector('.eyebrow').textContent = 'HABIT MANAGEMENT';
    form.querySelector('h2').id = 'habitModalTitle';

    // Remove color selection
    form.querySelector('.color-options').parentElement.remove();

    // Add Description after Habit Name
    $('#habitName').closest('label').insertAdjacentHTML(
        'afterend',
        '<label>Description<textarea id="habitDescription" maxlength="1000" placeholder="What does this habit involve?"></textarea></label>'
    );

    // Get existing fields
    const formRow = form.querySelector('.form-row');
    const categoryLabel = $('#habitCategory').closest('label');
    const frequencyLabel = $('#habitFrequency').closest('label');
    const goalLabel = $('#habitGoal').closest('label');

    formRow.parentNode.insertBefore(categoryLabel, formRow);

    formRow.appendChild(goalLabel);

    formRow.insertAdjacentHTML(
        'afterend',
        '<label>Status<select id="habitStatus"><option value="active">Active</option><option value="inactive">Inactive</option></select></label>' +
        '<p class="form-help">The start date is recorded automatically when the habit is created.</p>'
    );

    form.onsubmit = submitHabitForm;
}
function habitPayload() { return { habit_name: $('#habitName').value, description: $('#habitDescription').value, category: $('#habitCategory').value, goal_type: $('#habitFrequency').value, target_count: Number($('#habitGoal').value), status: $('#habitStatus').value }; }
function openHabitModal(habit = null) { const form = $('#habitForm'); form.reset(); form.dataset.habitId = habit ? habit.habit_id : ''; $('#habitModalTitle').textContent = habit ? 'Edit habit' : 'Create a habit'; if (habit) { $('#habitName').value = habit.habit_name; $('#habitDescription').value = habit.description || ''; $('#habitCategory').value = habit.category; $('#habitFrequency').value = habit.goal_type; $('#habitGoal').value = habit.target_count; $('#habitStatus').value = habit.status; } $('#modalBackdrop').classList.add('open'); $('#habitName').focus(); }
async function submitHabitForm(event) { event.preventDefault(); const form = event.currentTarget; const id = form.dataset.habitId; const button = form.querySelector('[type="submit"]'); button.disabled = true; try { const result = await habitRequest(id ? `/api/habits/${id}` : '/api/habits', id ? 'PUT' : 'POST', habitPayload()); $('#modalBackdrop').classList.remove('open'); toast(result.message); await Promise.all([loadHabits(), loadDashboard()]); } catch (error) { toast(error.message); } finally { button.disabled = false; } }
function renderHabitTable() { const body = $('#habitTableBody'), empty = $('#habitTableEmpty'); body.innerHTML = managedHabits.map(habit => `<tr><td>${escapeHtml(habit.habit_name)}<small>${escapeHtml(habit.description || 'No description')}</small></td><td>${escapeHtml(habit.category)}</td><td>${habit.goal_type}</td><td>${habit.target_count}</td><td>${habit.start_date}</td><td><span class="status-pill ${habit.status}">${habit.status}</span></td><td><div class="row-actions"><button class="row-button" data-habit-view="${habit.habit_id}">View</button><button class="row-button" data-habit-edit="${habit.habit_id}">Edit</button><button class="row-button" data-habit-toggle="${habit.habit_id}">${habit.status === 'active' ? 'Deactivate' : 'Activate'}</button><button class="row-button delete" data-habit-delete="${habit.habit_id}">Delete</button></div></td></tr>`).join(''); empty.hidden = managedHabits.length > 0; }
function updateCategoryFilter() { const select = $('#categoryFilter'), selected = select.value, categories = [...new Set(managedHabits.map(habit => habit.category))].sort(); select.innerHTML = '<option value="">All categories</option>' + categories.map(category => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join(''); select.value = categories.includes(selected) ? selected : ''; }
async function loadHabits() { try { const params = new URLSearchParams(); if ($('#habitSearch').value.trim()) params.set('search', $('#habitSearch').value.trim()); if ($('#categoryFilter').value) params.set('category', $('#categoryFilter').value); if ($('#goalTypeFilter').value) params.set('goal_type', $('#goalTypeFilter').value); if ($('#statusFilter').value) params.set('status', $('#statusFilter').value); const result = await habitRequest(`/api/habits?${params}`); managedHabits = result.habits; renderHabitTable(); updateCategoryFilter(); } catch (error) { console.error('Habits could not be loaded:', error); } }
async function showHabit(id) { try { const { habit } = await habitRequest(`/api/habits/${id}`); const detail = $('#habitDetail'); detail.hidden = false; detail.innerHTML = `<h3>${escapeHtml(habit.habit_name)}</h3><p>${escapeHtml(habit.description || 'No description provided.')}</p><p><strong>Category:</strong> ${escapeHtml(habit.category)} &nbsp; <strong>Goal:</strong> ${habit.target_count} ${habit.goal_type} &nbsp; <strong>Status:</strong> ${habit.status}</p>`; detail.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (error) { toast(error.message); } }
async function editHabit(id) { try { const { habit } = await habitRequest(`/api/habits/${id}`); openHabitModal(habit); } catch (error) { toast(error.message); } }
async function toggleHabit(id) { try { const { habit } = await habitRequest(`/api/habits/${id}`); habit.status = habit.status === 'active' ? 'inactive' : 'active'; const result = await habitRequest(`/api/habits/${id}`, 'PUT', habit); toast(result.message); await Promise.all([loadHabits(), loadDashboard()]); } catch (error) { toast(error.message); } }
async function deleteHabit(id) { if (!confirm('Delete this habit and its completion history? This cannot be undone.')) return; try { const result = await habitRequest(`/api/habits/${id}`, 'DELETE'); $('#habitDetail').hidden = true; toast(result.message); await Promise.all([loadHabits(), loadDashboard()]); } catch (error) { toast(error.message); } }
function localDateValue() { return new Date().toISOString().slice(0, 10); }
async function saveTrackerCompletion(id) { const checked=document.querySelector(`[data-track-check="${id}"]`).checked; const payload={habit_id:Number(id),completion_date:$('#trackerDate').value,completed:checked,completion_count:Number(document.querySelector(`[data-track-count="${id}"]`).value),notes:document.querySelector(`[data-track-notes="${id}"]`).value}; try { const result=await habitRequest('/api/tracker/completions','POST',payload); toast(result.message); await Promise.all([loadTracker(),loadDashboard(),loadAnalytics()]); } catch(error){toast(error.message);} }
async function generateReport(period) { try { const {report}=await habitRequest('/api/reports','POST',{period}); const root=$('#reportResult'); root.hidden=false; root.innerHTML=`<p class="eyebrow">${report.period.toUpperCase()} REPORT READY</p><h2>${report.summary.overall_completion_percentage}% overall completion</h2><div class="report-download"><a href="${report.csv_url}" download>Download CSV</a><a href="${report.txt_url}" download>Download TXT</a></div>`; toast('Report generated.'); }catch(error){toast(error.message);} }
async function generateCharts() { try { const {charts}=await habitRequest('/api/charts','POST',{}); $('#chartGrid').innerHTML=Object.entries(charts).map(([name,url])=>`<img src="${url}" alt="${name} chart">`).join(''); toast('Charts generated from current analytics.'); }catch(error){toast(error.message);} }
async function loadSettings(applyDefault = false) { try { const {preferences}=await habitRequest('/api/settings'); $('#settingTheme').value=preferences.theme; $('#settingView').value=preferences.default_dashboard_view; $('#settingWeekly').value=preferences.weekly_goal; $('#settingMonthly').value=preferences.monthly_goal; $('#settingExport').value=preferences.export_format; data.theme=preferences.theme; save(); applyTheme(); if(applyDefault && preferences.default_dashboard_view!=='dashboard') document.querySelector(`[data-view="${preferences.default_dashboard_view}"]`).click(); } catch(error){console.error('Settings could not be loaded:',error);} }
async function saveSettings(event) { event.preventDefault(); try { const {preferences,message}=await habitRequest('/api/settings','PUT',{theme:$('#settingTheme').value,default_dashboard_view:$('#settingView').value,weekly_goal:Number($('#settingWeekly').value),monthly_goal:Number($('#settingMonthly').value),export_format:$('#settingExport').value}); data.theme=preferences.theme; save(); applyTheme(); toast(message); await loadDashboard(); } catch(error){toast(error.message);} }
function renderTrackerCalendar() {
  const selected = $('#trackerDate').value || localDateValue();
  const base = new Date(`${selected}T12:00:00`);
  const days = Array.from({ length: 7 }, (_, index) => { const day = new Date(base); day.setDate(base.getDate() - 3 + index); return day; });
  $('#trackerCalendar').innerHTML = days.map(day => { const value = day.toISOString().slice(0, 10); return `<button type="button" class="calendar-day ${value === selected ? 'selected' : ''}" data-calendar-date="${value}"><span>${day.toLocaleDateString('en-US',{weekday:'short'})}</span><strong>${day.getDate()}</strong></button>`; }).join('');
}
async function loadTracker() {
  try {
    const selected = $('#trackerDate').value || localDateValue();
    const result = await habitRequest(`/api/tracker?date=${selected}`); const root = $('#trackerList'); renderTrackerCalendar();
    root.innerHTML = result.tracker.habits.map(h => `<article class="tracker-card ${h.state}"><h3>${escapeHtml(h.habit_name)}</h3><small>${escapeHtml(h.category)} · Target: ${h.target_count} per ${h.goal_type}</small><div class="tracker-controls"><label><input data-track-check="${h.habit_id}" type="checkbox" ${h.completed ? 'checked' : ''}> Complete target</label><input data-track-count="${h.habit_id}" type="number" min="0" max="1000" value="${h.completion_count || 0}" title="Completion count"><button data-track-save="${h.habit_id}">Save</button></div><small>${h.completed ? '✓ Goal reached' : `${h.completion_count || 0} / ${h.target_count} — ${h.state}`}</small><textarea data-track-notes="${h.habit_id}" maxlength="1000" placeholder="Optional notes">${escapeHtml(h.notes)}</textarea></article>`).join('') || '<div class="empty-state">No active habits for this date.</div>';
  } catch (error) { toast(error.message); }
}
async function loadAnalytics(period = insightDays) {
  try {
    const { analytics } = await habitRequest(`/api/analytics?period=${period}`); const metrics = analytics.habits;
    $('#performanceBars').innerHTML = metrics.map(h => `<div class="performance-row"><span>${escapeHtml(h.habit_name)}</span><div class="progress-track"><div class="progress-fill" style="width:${h.completion_percentage}%;background:#4d9ce8"></div></div><strong>${h.completion_percentage}%</strong></div>`).join('') || '<div class="empty-state">Complete habits to unlock analytics.</div>';
    const most = analytics.most_consistent; const least = analytics.least_consistent; $('#reportHeading').textContent = period === 7 ? 'This week’s analytics' : 'This month’s analytics'; $('#reportContent').innerHTML = `<div class="report-item"><strong>${analytics.overall_completion_percentage}% overall completion.</strong></div>${most ? `<div class="report-item"><strong>Most consistent:</strong> ${escapeHtml(most.habit_name)} (${most.consistency_score}%).</div><div class="report-item"><strong>Needs attention:</strong> ${escapeHtml(least.habit_name)} (${least.missed_days} missed days).</div>` : ''}`;
  } catch (error) { console.error(error); }
}
window.addEventListener('load', () => {
  document.querySelectorAll('.segment').forEach(button => button.addEventListener('click', () => { insightDays = Number(button.dataset.period); document.querySelectorAll('.segment').forEach(item => item.classList.toggle('active', item === button)); loadAnalytics(insightDays); }));
  document.addEventListener('click', event => { const day = event.target.closest('[data-calendar-date]'); if (day) { $('#trackerDate').value = day.dataset.calendarDate; loadTracker(); } });
});