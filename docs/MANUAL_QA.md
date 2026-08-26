# Omapath manual QA

Этот чеклист выполняется в реальном GTK4/Wayland-сеансе Omarchy. Для операций
создайте временный каталог; не проверяйте destructive actions на рабочих
данных.

## Запуск и окно

- [ ] `OMAPATH_DEBUG=1 ./run.sh` запускается без traceback; Home показывает
  реальные entries, а debug log подтверждает backend/store/selection/view count.
  `OMAPATH_TRACE_ITEMS=1` включать только для точечной диагностики bind.
- [ ] Непрерывно менять размер 1200→420 и обратно. На ~1100/800 должны
  переключаться только wide/medium/narrow состояния без дрожания. На
  высоте 360–400 не должно быть GtkPaned measurement warning, а после возврата
  к нормальной высоте terminal/status presentation должна восстановиться.
- [ ] Проверить 1400, 1000, 700 и 500 px по ширине: main FileView остаётся
  видимым, sidebar в narrow открывается drawer-кнопкой и Escape,
  breadcrumbs адаптивно сворачиваются, ellipsis открывает скрытые сегменты.
- [ ] В topbar видна одна компактная кнопка Panels с tooltip/focus. Её
  CheckButton-состояния Sidebar (F9), Folders (F7), Information (F11) и
  Terminal (F4) меняются сразу после открытия/скрытия панели.
- [ ] Нажатие каждой panel-кнопки реально меняет layout: Sidebar открывает
  wide panel или narrow drawer, F7 добавляет/removes folder tree, F11
  добавляет/removes Information, F4 показывает VTE или запускает внешний
  terminal при отсутствии VTE. Settings → Panels и topbar остаются
  синхронизированы.
- [ ] Проверить Icons, Compact, Details; F3 split; F7 folders; F11 info; F4
  terminal. Secondary panels скрыты по умолчанию.
- [ ] На Home открыть каталог с большим числом entries (например `/lib64`),
  включив `OMAPATH_DEBUG=1`: первый bounded chunk должен появиться быстро,
  окно не должно зависнуть, а финальный `layout event=commit` должен показать
  нормальные `grid_columns`, `bound_items` и `vadj_upper` без giant extent.
- [ ] После большого каталога вернуться в Home и проверить, что `vadj_upper`
  и scroll position принадлежат Home, а не предыдущему каталогу.
- [ ] Переключить активную Omarchy theme. При включённом Follow Omarchy theme
  палитра обновляется без потери location/selection.

## Navigation, tabs and selection

- [ ] Home → Documents → nested directory → Back → Forward → Up.
- [ ] Click the unused path/breadcrumb background (or press Ctrl+L). The
  single topbar field shows the real current path/URI, keeps the same topbar
  height, scrolls long paths horizontally, and does not alter individual
  breadcrumb left/middle/right-click behavior.
- [ ] In the location field test `/etc`, `~/Downloads`, `/`, a path with
  spaces, Cyrillic/Unicode, `file:///…`, and a relative value such as
  `../Pictures`. Enter navigates and returns focus to FileView; Escape cancels
  without navigation. With suggestions visible, the first Escape closes only
  suggestions. Up/Down selects, Tab completes, and Enter navigates.
- [ ] Enter a missing path, a regular file, and a readable-but-inaccessible
  folder. The field remains open with an inline `Location does not exist`,
  `Not a folder`, or `Permission denied` message and the current folder does
  not change. If GVfs is available, test `sftp://`, `smb://`, `dav://`,
  `davs://`, and `ftp://` through the provider.
- [ ] Open the field with an empty value/Down and verify manual-entry history
  is separate from Back/Forward and bounded to the latest 24 successful
  entries. While focused, Ctrl+A/C/X/V/Delete edit the Gtk.Entry and do not
  invoke file operations; Ctrl+F still opens Search separately.
- [ ] Один click выбирает, double click/Enter открывает в том же tab, middle
  click folder открывает новый tab.
- [ ] Ctrl+click/Shift+click, Ctrl+A, Ctrl+Shift+A, marker (если включён),
  right-click по одному и нескольким выбранным элементам.
- [ ] Ctrl+T, Ctrl+Tab, Ctrl+Shift+Tab, Ctrl+W, Ctrl+Shift+T; middle-click
  закрывает tab; drag tab reorder; right-click проверяет Duplicate, Move Left,
  Move Right, Close и Close Others/Left/Right.
- [ ] Переключение tab сохраняет location, history, view, sort, hidden/groups,
  selection и scroll; после перезапуска session restore не смешивает tab state.

## Recent, search and panels

- [ ] Sidebar содержит одну стабильную строку Recent; переходы по каталогам не
  добавляют новые строки. Открыть Recent и проверить Back/Forward как обычную
  виртуальную локацию.
- [ ] Нажать видимую Search-кнопку в topbar и проверить поиск без знания
  shortcut; Ctrl+F открывает тот же режим, Esc возвращает breadcrumbs. В
  options проверить Current folder/Subfolders, Case sensitive, Include hidden
  и Inside text; Ctrl+I остаётся отдельным локальным Filter.
- [ ] При отсутствии VTE нет постоянной terminal-панели или зарезервированной
  высоты; F4/контекст Open Terminal Here запускает внешний terminal. При
  наличии VTE F4 показывает embedded terminal в вертикальном resizable pane.
- [ ] Потянуть sidebar, Folders (если включён), Information (F11), split и VTE
  divider (если доступен); закрыть/открыть окно и проверить сохранение размеров.
- [ ] В narrow включить F7/F11/F4: панели не должны навязывать main
  area minimum; user settings (`show_sidebar`, inspector, terminal) не должны
  самопроизвольно сбрасываться и должны вернуться после widen.

## Groups and DnD

- [ ] Включить Show in Groups и выбрать Type/Name/Date/Size. Header показывает
  label/count, click сворачивает/разворачивает, right-click даёт Group By и
  Ungroup. После внешнего create/delete group count обновляется.
- [ ] Перетащить file/folder на folder row, пустой background, breadcrumb,
  sidebar Place, pinned row, mounted device, folder-tree row, inactive tab,
  split pane и Trash.
- [ ] Проверить accent highlight только для допустимого target; invalid target
  не подсвечивается. Same-device default Move, cross-device/provider Copy,
  Ctrl Copy, Shift Move, Alt Link (локальные файлы).
- [ ] Проверить Omapath ↔ внешний GTK file manager через URI-list; имена с
  пробелами, кириллицей, Unicode и `;` не повреждаются. Folder в самого себя
  или child отклоняется без операции.
- [ ] Проверить sidebar pinned reorder и сохранение порядка.

## File operations and conflicts

- [ ] Ctrl+C/X/V для одного и нескольких файлов; исчезнувший source выдаёт
  понятную ошибку и не создаёт target.
- [ ] F2/inline rename: stem selection, Enter/Escape, пробелы/Unicode, case-only
  rename и collision.
- [ ] New Folder/New File, Duplicate, Link, Open, Open With, Properties, Move
  to Trash, Restore and permanent delete.
- [ ] Конфликт файла: default Skip, Keep Both, Replace, Cancel. Conflict
  directory: Merge, Rename Incoming, Skip, Cancel. Apply-to-all не переносит
  file decision на directory class.
- [ ] Operation center показывает current item/progress, Cancel, failure Details
  и Undo для reversible operations. Permanent delete/Empty Trash не показывают
  fake Undo.
- [ ] Ctrl+Z/Ctrl+Shift+Z реально undo/redo copy, move, rename, create, link,
  trash и archive operations, где metadata безопасна.

## Preview, properties, Git and remote

- [ ] Space на image/text/folder/archive; Left/Right adjacent, Escape/Space
  close; large text показывает bounded prefix и Open full file.
- [ ] Corrupt/unsupported/PDF/audio/video preview деградирует в понятный
  fallback без traceback. При наличии GdkPixbuf image thumbnail не применяет
  устаревший результат к другой recycled row.
- [ ] F11 показывает selection count, size, MIME, path, timestamps, permissions,
  symlink target; tags/rating/comment сохраняются и появляются в sidebar.
- [ ] В тестовом Git repo включить Show Git Status: rows получают subtle
  decoration; context Git Status/Diff/Log/Stage/Unstage/Restore и background
  Commit/Fetch/Pull/Push не запускаются в обычной папке.
- [ ] При установленном GVfs проверить SFTP/SMB/WebDAV: provider capability
  controls разрешают только поддержанные create/copy/move/rename/trash/delete.
  Remote mutations не выдаются за локально undoable.
- [ ] Подключить removable volume: mount, navigate, unmount, eject/stop при
  доступности GIO. Отключение устройства не оставляет stale callback.

## Settings and accessibility

- [ ] Settings → Confirmations → File conflicts меняет ask/skip/keep both/
  replace; видимые controls действительно меняют runtime.
- [ ] Проверить theme, hidden, sorting, folders-first, groups, previews,
  selection marker, tooltips, inline rename, sidebar/panels, refresh, split,
  session, shortcuts and external clipboard.
- [ ] Keyboard focus видим; Tab/Space/Enter работают в Settings, palette,
  context menu и operation center; disabled capability actions не кликаются.

При удалении в Trash дополнительно проверить отсутствие GLib-GIO-CRITICAL и
Gtk-CRITICAL после обновления списка; context popover не должен ссылаться на
удалённый file item.

В headless-окружении выполнить `python3 -m compileall -q omapath tests` и
`python3 -m unittest discover -s tests -v`. Эти команды не заменяют реальную
проверку rendering, pointer hit-testing, DnD cursors, Wayland окон и VTE.
