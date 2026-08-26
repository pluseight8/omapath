# Omapath final visual QA

Проверять вручную на реальном GTK4/Wayland Omarchy/Hyprland. Headless-тесты
проверяют только структуру, CSS и модель; они не подтверждают визуальный баланс,
focus, hover или реальный pointer input.

## TOPBAR

- [ ] Height balanced: примерно 42–46 px.
- [ ] Back / Forward / Up компактные, одинаковые, с click target около 32–36 px.
- [ ] Disabled states читаемы, но явно muted.
- [ ] Idle topbar спокойный, без постоянных рамок вокруг каждой кнопки.
- [ ] В topbar остаётся одна кнопка Panels с hit target 32–34 px, tooltip и
  видимым keyboard focus; активные состояния панелей читаются в её popover.

## BREADCRUMBS

- [ ] Breadcrumbs занимают главное пространство topbar.
- [ ] Long path collapses middle segments first.
- [ ] Каждый видимый breadcrumb остаётся кликабельным; ellipsis открывает
      список скрытых сегментов и поддерживает left/right/middle click.
- [ ] No overflow: controls и item count остаются в viewport.

## SIDEBAR

- [ ] Width около 190–210 px на обычном окне и не растёт на large/ultrawide.
- [ ] Rows не выглядят как giant buttons; высота примерно 32–36 px.
- [ ] Active item restrained: subtle accent tint и тонкий левый indicator.
- [ ] Icons aligned, 16–18 px, gap с текстом около 8 px.
- [ ] Section spacing consistent; section title показывается только для реальной секции.
- [ ] Нет giant gaps между `Filesystem` и `Trash`.
- [ ] `PLACES`/`DEVICES` читаемы и не исчезают из-за слишком низкой opacity.

## GRID

- [ ] Left aligned.
- [ ] Cells примерно 110–128 px, без растягивания на wide displays.
- [ ] Icons 48 px.
- [ ] Filenames максимум 2 lines, centered, Unicode/кириллица читаются.
- [ ] Grid не выглядит чрезмерно sparse; horizontal gap около 12–16 px.
- [ ] Hover clear, но neutral и без лишних border/shadow.
- [ ] Selection заметнее hover и построена от текущего Omarchy accent.
- [ ] Keyboard focus clear и отличается от selected state.
- [ ] Multi-selection визуально различима на каждом выбранном item.
- [ ] Empty folder показывает `This folder is empty` без иллюстрации/card.
- [ ] Внутри FileView нет giant vertical gaps: GridView занимает реальную
      ширину, rows имеют естественную высоту, прокрутка начинается сразу.
- [ ] Large directory first paint не блокирует окно; после завершения загрузки
      scroll extent соответствует числу строк и колонок, без stale extent.

## RESPONSIVE

- [ ] 1400 px: `responsive-wide`, постоянный sidebar, все Details columns.
- [ ] 1000 px: `responsive-medium`, плотный topbar, без лишних Details columns.
- [ ] 700 px: `responsive-narrow`, sidebar уходит в overlay/drawer, main area
      не получает его minimum; Forward доступен через shortcut/overflow.
- [ ] 500 px: `responsive-narrow`, одна GridView column при необходимости,
      Details показывает только Name, status bar/terminal не съедают высоту.
- [ ] Continuous resize 1200→420 и обратно не дёргает layout на каждом пикселе;
      state changes происходят только на hysteresis breakpoints.
- [ ] Heights 600→360→600: terminal/status presentation collapses temporarily
      and restores after widening without changing their settings intent.
- [ ] No control leaves the viewport.
- [ ] `Info` и `Terminal` не резервируют место при запуске; `Panels` включает
      их только по запросу.
- [ ] Gtk warnings отсутствуют, особенно `Trying to measure GtkPaned ...`; на
      реальном Omarchy проверять также после открытия F7/F11/F4.
- [ ] Нет permanent inspector, preview, terminal, second toolbar или status dashboard.

## REGRESSION

- [ ] Right click по file/folder и пустому месту всё ещё открывает меню.
- [ ] Ctrl+C.
- [ ] Ctrl+V.
- [ ] Ctrl+X.
- [ ] F2.
- [ ] Delete → Trash.
- [ ] Create Folder.
- [ ] Create File.
- [ ] Back / Forward / Up и Enter сохраняют рабочее поведение.
- [ ] Normal use не добавляет Traceback, TypeError, AttributeError, Gtk-CRITICAL,
      Gtk-WARNING, `GTK_IS_WIDGET`, `GTK_IS_ACCESSIBLE` или duplicate-item warning.

## THEME

- [ ] Active Omarchy background, surface, foreground, muted, accent и font применены.
- [ ] Нет hardcoded Everforest, `#7fbbb3` или `iA Writer Mono S`.
- [ ] Muted text остаётся читаемым в текущей теме.
