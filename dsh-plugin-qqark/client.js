/**
 * dsh-plugin-qqark — 左侧边栏 UI（浏览器侧）。
 *
 * 加载约定：Harness 用 window.__ModuleLoader__.load 装客户端模块，
 * factory 里拿到的是受限的 require（只给 react / react/jsx-runtime），
 * 不能 require 任何 Harness Client 包。样式一律走 --dsw-alias-* token，
 * 不写死颜色，这样浅色/深色主题都能自动跟随。
 *
 * 红线：只渲染 slot 里的 React 组件，禁止用内嵌网页（frame）托管整页。
 */

window.__ModuleLoader__.load({
  id: 'dsh-plugin-qqark',
  factory: function (require) {
    var module = { exports: {} }

    const React = require('react')
    const h = React.createElement

    /** 面板 id：sidebar 图标行与 main 整页共用，必须一致。 */
    const PANEL_ID = 'qqark'
    /** 侧边栏行序（Plugins 0 / Schedule 10 / board 20 / 技能中心 30）。 */
    const PANEL_ORDER = 40

    /** 同源、文档相对路径：GUI 用 <base href="./">，不能带前导斜杠。 */
    const API = 'api/dsh-qqark'

    // ---------- 小工具 ----------

    function fmtSize(bytes) {
      const n = Number(bytes) || 0
      if (n < 1024) return n + ' B'
      const units = ['KB', 'MB', 'GB', 'TB']
      let v = n / 1024
      let i = 0
      while (v >= 1024 && i < units.length - 1) {
        v /= 1024
        i += 1
      }
      return v.toFixed(v >= 100 ? 0 : 1) + ' ' + units[i]
    }

    function fmtTime(sec) {
      const n = Number(sec) || 0
      if (!n) return '-'
      const d = new Date(n * 1000)
      if (isNaN(d.getTime())) return '-'
      const p = (x) => String(x).padStart(2, '0')
      return (
        d.getFullYear() +
        '-' +
        p(d.getMonth() + 1) +
        '-' +
        p(d.getDate()) +
        ' ' +
        p(d.getHours()) +
        ':' +
        p(d.getMinutes())
      )
    }

    function fmtAgo(sec) {
      const n = Number(sec) || 0
      if (!n) return '从未'
      const diff = Math.max(0, Math.floor(Date.now() / 1000 - n))
      if (diff < 60) return '刚刚'
      if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前'
      if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前'
      return Math.floor(diff / 86400) + ' 天前'
    }

    async function apiGet(path) {
      const res = await fetch(API + path, { headers: { accept: 'application/json' } })
      const body = await res.json().catch(() => ({ ok: false, error: 'HTTP ' + res.status }))
      if (body && body.ok === false) throw new Error(body.error || '请求失败')
      return body
    }

    async function apiPost(path, payload) {
      const res = await fetch(API + path, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload || {}),
      })
      const body = await res.json().catch(() => ({ ok: false, error: 'HTTP ' + res.status }))
      if (body && body.ok === false) throw new Error(body.error || '请求失败')
      return body
    }

    // ---------- 设计 token 快捷方式 ----------

    const C = {
      bg1: 'var(--dsw-alias-bg-layer-1, transparent)',
      bg2: 'var(--dsw-alias-bg-layer-2, rgba(127,127,127,.06))',
      bg3: 'var(--dsw-alias-bg-layer-3, rgba(127,127,127,.1))',
      border1: 'var(--dsw-alias-border-l1, rgba(127,127,127,.18))',
      border2: 'var(--dsw-alias-border-l2, rgba(127,127,127,.28))',
      label1: 'var(--dsw-alias-label-primary)',
      label2: 'var(--dsw-alias-label-secondary)',
      label3: 'var(--dsw-alias-label-tertiary)',
      brand: 'var(--dsw-alias-brand-primary)',
      onBrand: 'var(--dsw-alias-label-primary-foreground, #fff)',
      hover: 'var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.12))',
      active: 'var(--dsw-alias-interactive-bg-active, rgba(127,127,127,.18))',
      error: 'var(--dsw-alias-state-error-primary)',
      success: 'var(--dsw-alias-state-success-primary)',
      warn: 'var(--dsw-alias-state-warn-primary)',
      mono: 'var(--dsw-alias-mono-font-family, ui-monospace, SFMono-Regular, Menlo, monospace)',
    }

    // ---------- 基础样式 ----------

    const S = {
      page: {
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        background: C.bg1,
        color: C.label1,
        fontSize: 13,
        lineHeight: 1.55,
      },
      header: {
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '13px 20px 11px',
        borderBottom: '1px solid ' + C.border1,
        background: C.bg1,
        flex: 'none',
      },
      titleWrap: { display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 },
      title: { fontSize: 15, fontWeight: 700, letterSpacing: '.1px', lineHeight: 1.3 },
      subtitle: { fontSize: 11, color: C.label3, lineHeight: 1.4 },
      // 下划线指示器式标签栏：比实心品牌色块轻，切换时不会整条彩带闪。
      tabs: {
        display: 'flex',
        gap: 2,
        padding: '0 16px',
        borderBottom: '1px solid ' + C.border1,
        flex: 'none',
        overflowX: 'auto',
        background: C.bg1,
      },
      body: { flex: 1, overflow: 'auto', padding: '18px 20px 32px', minHeight: 0 },
      // 页面级纵向节奏：所有 Tab 的最外层都套这个，间距统一由它决定。
      stack: { display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 },
      muted: { color: C.label3, fontSize: 12 },
      err: { color: C.error, fontSize: 12 },
      ok: { color: C.success, fontSize: 12 },
      card: {
        border: '1px solid ' + C.border1,
        borderRadius: 12,
        padding: 15,
        background: C.bg2,
      },
      // 响应式指标网格：宽度够就四列，窄了自动降到两列，不会像 flex-wrap
      // 那样出现「3 个一行 + 1 个孤零零」的参差。
      statGrid: {
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(158px, 1fr))',
        gap: 12,
      },
      // 两栏内容区（概览的「登录账号 / 按类型」）。
      twoCol: {
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(268px, 1fr))',
        gap: 14,
        alignItems: 'start',
      },
      input: {
        padding: '7px 10px',
        borderRadius: 8,
        border: '1px solid ' + C.border2,
        background: C.bg1,
        color: C.label1,
        fontSize: 12,
        outline: 'none',
        transition: 'border-color .15s ease',
      },
      // 窄面板用的列表行。侧边栏只有 460px 左右，六列表格实测要横向滚 76px
      // （文件名一长还要更宽），表格在这种情况下就是排版事故：用户必须左右拖
      // 才能看全一行。所以群聊、文件、任务三页全部改用「一行一条」的堆叠布局——
      // 主信息一行、次要信息缩到第二行，宽度天然够，也不需要横向滚动。
      // （原先的 th / td / TableWrap 已随之删除：留着只会诱使人再退回表格。）
      row: {
        display: 'flex',
        alignItems: 'flex-start',
        gap: 9,
        padding: '9px 12px',
        borderBottom: '1px solid ' + C.border1,
      },
      rowMain: { display: 'flex', flexDirection: 'column', gap: 3, flex: 1, minWidth: 0 },
      rowTitle: {
        fontSize: 12.5,
        fontWeight: 600,
        color: C.label1,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      },
      rowMeta: {
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        flexWrap: 'wrap',
        fontSize: 11,
        color: C.label3,
        lineHeight: 1.45,
      },
      rowSide: { flex: 'none', display: 'flex', alignItems: 'center', gap: 6 },
      link: { color: 'var(--dsw-alias-link, ' + C.brand + ')', cursor: 'pointer', textDecoration: 'none' },
      mono: { fontFamily: C.mono, fontVariantNumeric: 'tabular-nums' },
    }

    // ---------- 图标 ----------

    function svg(paths, opts) {
      const o = opts || {}
      return h(
        'svg',
        {
          width: o.size || 16,
          height: o.size || 16,
          viewBox: '0 0 16 16',
          fill: 'none',
          stroke: 'currentColor',
          strokeWidth: o.width || 1.4,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
          'aria-hidden': true,
          style: { display: 'block', flex: 'none' },
        },
        paths.map((d, i) => h('path', { key: i, d })),
      )
    }

    const Ico = {
      archive: (o) => svg(['M2.5 4.5h4l1.2 1.6h5.8v7.4a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1z', 'M8 8.2v3.2', 'M6.6 10.2 8 11.6l1.4-1.4'], o),
      refresh: (o) => svg(['M13.2 8a5.2 5.2 0 1 1-1.6-3.7', 'M13.4 2.6v3h-3'], o),
      play: (o) => svg(['M6 4.4 11.6 8 6 11.6z'], o),
      power: (o) => svg(['M8 2.6v5', 'M12.3 4.6a5.2 5.2 0 1 1-8.6 0'], o),
      shield: (o) => svg(['M8 2.4 13 4.2v3.9c0 3-2.1 5-5 5.5-2.9-.5-5-2.5-5-5.5V4.2z', 'M6 8.1l1.4 1.4L10.2 6.6'], o),
      folder: (o) => svg(['M2.2 4.6h4l1.2 1.6h6.4v6.2a1 1 0 0 1-1 1H3.2a1 1 0 0 1-1-1z'], o),
      search: (o) => svg(['M7.2 12.4a5.2 5.2 0 1 0 0-10.4 5.2 5.2 0 0 0 0 10.4z', 'M11 11l2.6 2.6'], o),
      users: (o) => svg(['M6 7.4a2.4 2.4 0 1 0 0-4.8 2.4 2.4 0 0 0 0 4.8z', 'M1.8 13.4c0-2.2 1.9-3.6 4.2-3.6s4.2 1.4 4.2 3.6', 'M11 5.2a2 2 0 0 1 0 3.9', 'M11.6 9.9c1.6.3 2.6 1.5 2.6 3.5'], o),
      download: (o) => svg(['M8 2.4v7', 'M5 6.8 8 9.8l3-3', 'M2.8 12.6h10.4'], o),
      alert: (o) => svg(['M8 2.6 14.4 13H1.6z', 'M8 6.4v3', 'M8 11.1v.1'], o),
      check: (o) => svg(['M3.2 8.4 6.4 11.6l6.4-6.6'], o),
      coin: (o) => svg(['M8 13.6c3.1 0 5.6-2.5 5.6-5.6S11.1 2.4 8 2.4 2.4 4.9 2.4 8s2.5 5.6 5.6 5.6z', 'M8 5.4v5.2', 'M9.6 6.6H7.4a1.1 1.1 0 0 0 0 2.2h1.2a1.1 1.1 0 0 1 0 2.2H6.2'], o),
      clock: (o) => svg(['M8 13.6c3.1 0 5.6-2.5 5.6-5.6S11.1 2.4 8 2.4 2.4 4.9 2.4 8s2.5 5.6 5.6 5.6z', 'M8 4.8V8l2.2 1.4'], o),
      external: (o) => svg(['M9.4 2.6h4v4', 'M13.4 2.6 7.6 8.4', 'M11.6 9.6v3.2a1 1 0 0 1-1 1H3.2a1 1 0 0 1-1-1V5.4a1 1 0 0 1 1-1h3.2'], o),
      // 以下为分类/视图用的图标，路径刻意画得极简，13px 下也不糊。
      doc: (o) => svg(['M4 2.4h5l3 3v8.2a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3.4a1 1 0 0 1 1-1z', 'M9 2.6v3h3', 'M5.4 8.2h5.2', 'M5.4 10.6h5.2'], o),
      sheet: (o) => svg(['M3 3.4h10v9.2H3z', 'M3 6.6h10', 'M3 9.6h10', 'M6.4 3.4v9.2'], o),
      slide: (o) => svg(['M2.4 3.4h11.2v7.2H2.4z', 'M8 10.6v2.8', 'M5.8 13.4 8 11.8l2.2 1.6'], o),
      code: (o) => svg(['M5.6 5 3 8l2.6 3', 'M10.4 5 13 8l-2.6 3', 'M9 3.4 7 12.6'], o),
      image: (o) => svg(['M2.6 3.4h10.8v9.2H2.6z', 'M2.6 10.2 6 7.2l2.6 2.2 2-1.6 3.4 2.6'], o),
      music: (o) => svg(['M6 11.4V4.6l6-1.4v6.4', 'M4 13.2a2 2 0 1 0 0-4 2 2 0 0 0 0 4z', 'M10 11.8a2 2 0 1 0 0-4 2 2 0 0 0 0 4z'], o),
      zip: (o) => svg(['M3 2.6h10v10.8H3z', 'M8 2.6v2', 'M6.8 4.6h2.4v1.8H6.8z', 'M6.8 6.4h2.4v1.8H6.8z', 'M7.4 8.2h1.2v1.6H7.4z'], o),
      app: (o) => svg(['M3 3h4.2v4.2H3z', 'M8.8 3H13v4.2H8.8z', 'M3 8.8h4.2V13H3z', 'M8.8 8.8H13V13H8.8z'], o),
      grid: (o) => svg(['M2.8 2.8h4.4v4.4H2.8z', 'M8.8 2.8h4.4v4.4H8.8z', 'M2.8 8.8h4.4v4.4H2.8z', 'M8.8 8.8h4.4v4.4H8.8z'], o),
      list: (o) => svg(['M2.8 4.4h10.4', 'M2.8 8h10.4', 'M2.8 11.6h6.6'], o),
      chevron: (o) => svg(['M6.2 4.2 10 8l-3.8 3.8'], o),
      tag: (o) => svg(['M2.6 8.4V3.4a.8.8 0 0 1 .8-.8h5l5 5-5.8 5.8z', 'M5.6 5.6h.02'], o),
    }

    // ---------- 通用组件 ----------

    /** 带 hover 反馈的按钮（内联样式做不了 :hover，只能自己管状态）。 */
    function Btn(props) {
      const [hover, setHover] = React.useState(false)
      const variant = props.variant || 'default'
      const disabled = !!props.disabled

      let base = {
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 6,
        padding: props.small ? '4px 10px' : '7px 14px',
        borderRadius: 8,
        border: '1px solid ' + C.border2,
        background: C.bg2,
        color: C.label1,
        cursor: disabled ? 'not-allowed' : 'pointer',
        fontSize: props.small ? 11 : 12,
        fontWeight: 500,
        opacity: disabled ? 0.5 : 1,
        transition: 'background .15s ease, border-color .15s ease, transform .08s ease',
        whiteSpace: 'nowrap',
        fontFamily: 'inherit',
      }
      if (variant === 'primary') {
        base = { ...base, border: '1px solid transparent', background: C.brand, color: C.onBrand, fontWeight: 600 }
      } else if (variant === 'ghost') {
        base = { ...base, border: '1px solid transparent', background: 'transparent', color: C.label2 }
      } else if (variant === 'danger') {
        base = { ...base, color: C.error, borderColor: C.error, background: 'transparent' }
      }
      if (hover && !disabled) {
        base.background = variant === 'primary' ? C.brand : variant === 'ghost' ? C.hover : C.bg3
        if (variant !== 'primary') base.borderColor = C.border2
        if (variant === 'primary') base.opacity = 0.9
      }

      return h(
        'button',
        {
          type: 'button',
          style: base,
          disabled,
          title: props.title || undefined,
          onMouseEnter: () => setHover(true),
          onMouseLeave: () => setHover(false),
          onClick: disabled ? undefined : props.onClick,
        },
        props.icon || null,
        props.children,
      )
    }

    /** 开关。checked 时滑块移到右侧并染成品牌色。 */
    function Switch(props) {
      const checked = !!props.checked
      const disabled = !!props.disabled
      return h(
        'button',
        {
          type: 'button',
          role: 'switch',
          'aria-checked': checked,
          disabled,
          title: props.title || undefined,
          onClick: disabled ? undefined : () => props.onChange(!checked),
          style: {
            position: 'relative',
            width: 44,
            height: 24,
            flex: 'none',
            borderRadius: 999,
            border: '1px solid ' + (checked ? 'transparent' : C.border2),
            background: checked ? C.brand : C.bg3,
            cursor: disabled ? 'not-allowed' : 'pointer',
            opacity: disabled ? 0.55 : 1,
            padding: 0,
            transition: 'background .2s ease',
          },
        },
        h('span', {
          style: {
            position: 'absolute',
            top: 2,
            left: checked ? 22 : 2,
            width: 18,
            height: 18,
            borderRadius: 999,
            background: checked ? C.onBrand : C.label3,
            transition: 'left .2s cubic-bezier(.4,0,.2,1)',
            boxShadow: '0 1px 3px rgba(0,0,0,.22)',
          },
        }),
      )
    }

    function Card(props) {
      return h(
        'div',
        { style: { ...S.card, ...(props.style || null) } },
        props.title || props.extra
          ? h(
              'div',
              {
                style: {
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  marginBottom: 11,
                  paddingBottom: props.title ? 9 : 0,
                  borderBottom: props.title ? '1px solid ' + C.border1 : 'none',
                  minHeight: 20,
                },
              },
              props.title ? h('div', { style: { fontSize: 12, fontWeight: 700, letterSpacing: '.1px' } }, props.title) : null,
              h('div', { style: { marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' } }, props.extra || null),
            )
          : null,
        props.children,
      )
    }

    function Stat(props) {
      const tone = props.tone || 'brand'
      const color =
        tone === 'ok' ? C.success : tone === 'bad' ? C.error : tone === 'warn' ? C.warn : C.brand
      return h(
        'div',
        {
          style: {
            ...S.card,
            // 尺寸由父级网格决定（S.statGrid），这里不再自己声明 flex/minWidth，
            // 否则在窄栏里会和 grid 的最小列宽打架、把卡片撑出容器。
            padding: '12px 14px',
            borderLeft: '3px solid ' + color,
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
            minWidth: 0,
          },
        },
        h(
          'div',
          { style: { display: 'flex', alignItems: 'center', gap: 6, color: C.label3 } },
          props.icon ? h('span', { style: { color, display: 'flex' } }, props.icon) : null,
          h('span', { style: { fontSize: 11, fontWeight: 600, letterSpacing: '.2px' } }, props.label),
        ),
        h('div', { style: { ...S.mono, fontSize: 21, fontWeight: 700, letterSpacing: '-.2px' } }, props.value),
        props.hint ? h('div', { style: { fontSize: 11, color: C.label3 } }, props.hint) : null,
      )
    }

    function Pill(props) {
      const tone = props.tone
      const color = tone === 'ok' ? C.success : tone === 'bad' ? C.error : tone === 'warn' ? C.warn : C.label2
      return h(
        'span',
        {
          style: {
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            color,
            fontSize: 11,
            fontWeight: 600,
            padding: '1px 7px',
            borderRadius: 999,
            border: '1px solid ' + color,
            opacity: 0.92,
            whiteSpace: 'nowrap',
          },
        },
        props.children,
      )
    }

    function Dot(props) {
      const color = props.tone === 'ok' ? C.success : props.tone === 'bad' ? C.error : C.label3
      return h('span', {
        style: {
          display: 'inline-block',
          width: 7,
          height: 7,
          borderRadius: 999,
          background: color,
          flex: 'none',
          boxShadow: props.tone === 'ok' ? '0 0 0 3px rgba(46,160,67,.18)' : 'none',
        },
      })
    }

    function Empty(props) {
      return h(
        'div',
        {
          style: {
            padding: '28px 16px',
            textAlign: 'center',
            color: C.label3,
            fontSize: 12,
            border: '1px dashed ' + C.border2,
            borderRadius: 10,
          },
        },
        props.text,
        props.hint ? h('div', { style: { marginTop: 6, fontSize: 11, opacity: 0.85 } }, props.hint) : null,
      )
    }

    function Loading(props) {
      return h('div', { style: { ...S.muted, padding: '18px 0' } }, props.text || '读取中…')
    }

    /** 进度条。 */
    function Bar(props) {
      const pct = Math.max(0, Math.min(100, Number(props.pct) || 0))
      return h(
        'div',
        {
          style: {
            height: 6,
            borderRadius: 999,
            background: C.bg3,
            overflow: 'hidden',
            flex: 1,
            minWidth: 60,
          },
        },
        h('div', {
          style: {
            width: pct + '%',
            height: '100%',
            background: props.tone === 'bad' ? C.error : C.brand,
            transition: 'width .3s ease',
          },
        }),
      )
    }

    /**
     * 下划线式标签按钮。抽成组件是因为要管 hover——内联样式写不了 :hover，
     * 只好用 state 记一下鼠标在不在上面。
     */
    function TabButton(props) {
      const [hover, setHover] = React.useState(false)
      const active = !!props.active
      return h(
        'button',
        {
          type: 'button',
          style: {
            display: 'inline-flex',
            alignItems: 'center',
            gap: 5,
            padding: '9px 12px 8px',
            marginBottom: -1,
            borderRadius: 0,
            border: 'none',
            // 指示条：透明 → 品牌色，位置固定在底边，所以切换时只有一条线在动。
            borderBottom: '2px solid ' + (active ? C.brand : 'transparent'),
            background: 'transparent',
            color: active ? C.label1 : hover ? C.label1 : C.label3,
            fontSize: 12,
            fontWeight: active ? 700 : 500,
            cursor: 'pointer',
            transition: 'color .15s ease, border-color .15s ease',
            whiteSpace: 'nowrap',
            fontFamily: 'inherit',
          },
          onClick: props.onClick,
          onMouseEnter: () => setHover(true),
          onMouseLeave: () => setHover(false),
        },
        h('span', { style: { display: 'flex', opacity: active ? 1 : 0.7 } }, props.icon),
        props.label,
      )
    }

    // ---------- 归档模式卡片（一键切号 + 断外挂 + 起停归档器） ----------

    /**
     * 一个开关做三件事：把 NapCat 切到归档号、把外部机器人（AstrBot）的通道切断、
     * 把归档器拉起来。关掉时全部反向还原。中途的每一步都列出来，失败时能直接看到
     * 卡在哪一步，而不是只给一句「失败了」。
     */
    function ArchiveModeCard(props) {
      const { state, loading, busy, result, onToggle, launcher, launching, onLaunch, admin, setAdmin } = props
      const on = !!(state && state.mode === 'archive')
      const [confirmOff, setConfirmOff] = React.useState(false)
      const running = !!(launcher && launcher.running)
      const batOk = !!(launcher && launcher.batOk)
      // 两个号没有内置默认值：账号是每个人自己的，插件不该替你猜。
      // 没填时显示「未设置」，点开关会直接被后端挡下来并提示去设置页填。
      const grabUin = (state && state.grabAccount) || '（未设置）'
      const quietUin = (state && state.quietAccount) || '（未设置）'
      const hasExternal = !!(state && state.grabHasExternal)
      const currentUin = (state && state.currentUin) || ''
      const currentNick = (state && state.currentNick) || ''

      const toggle = (next) => {
        if (busy || loading) return
        if (!next) {
          // 关闭会重启 NapCat 把号切回去，先让用户确认一次
          if (!confirmOff) {
            setConfirmOff(true)
            setTimeout(() => setConfirmOff(false), 5000)
            return
          }
        }
        setConfirmOff(false)
        onToggle(next)
      }

      const steps = (result && Array.isArray(result.steps)) ? result.steps : []
      const swRunning = !!(result && result.running)
      const failed = !!(result && result.ok === false)
      const archiverUp = running || !!(state && state.archiverRunning)

      return h(
        'div',
        {
          style: {
            ...S.card,
            padding: '15px 16px',
            display: 'flex',
            flexDirection: 'column',
            gap: 11,
            borderLeft: '3px solid ' + (on ? C.brand : C.border2),
          },
        },

        // 第一行：标题 + 开关
        h(
          'div',
          { style: { display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' } },
          h(
            'div',
            { style: { display: 'flex', alignItems: 'center', gap: 10, flex: '1 1 240px', minWidth: 200 } },
            h('span', { style: { color: on ? C.brand : C.label3, display: 'flex' } }, Ico.shield({ size: 22 })),
            h(
              'div',
              { style: { display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 } },
              h(
                'div',
                { style: { display: 'flex', alignItems: 'center', gap: 7 } },
                h('span', { style: { fontSize: 13, fontWeight: 700 } }, on ? '归档模式已开启' : '归档模式已关闭'),
                h(Dot, { tone: on ? 'ok' : 'idle' }),
              ),
              h(
                'div',
                { style: { fontSize: 11, color: C.label3, lineHeight: 1.5 } },
                on
                  ? '当前挂 ' + grabUin + ' 专门收文件，外部机器人已被切断。'
                  : '当前挂 ' + quietUin + ' 日常使用，归档器不运行。',
              ),
            ),
          ),
          h(Switch, {
            checked: on,
            disabled: busy || loading,
            onChange: toggle,
            title: on ? '关闭并切回日常号' : '开启：切到归档号、切断外部机器人并启动归档器',
          }),
        ),

        // 第一行补充：只在「等着二次确认」时出现，替代原先那个和开关重复的按钮。
        confirmOff
          ? h(
              'div',
              {
                style: {
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 11,
                  color: C.warn,
                  marginTop: -4,
                },
              },
              h('span', { style: { display: 'flex' } }, Ico.alert({ size: 12 })),
              '关闭会把号切回日常号并重启 NapCat，再点一次开关确认（5 秒内有效）。',
            )
          : null,

        // 第二行：状态徽标
        h(
          'div',
          { style: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' } },
          h(Pill, { tone: currentUin === grabUin && on ? 'ok' : 'idle' },
            'NapCat 登录：' + (currentUin ? currentUin + ' ' + (currentNick || '') : '未取到')),
          h(Pill, { tone: hasExternal ? 'bad' : 'ok' },
            hasExternal ? '外部机器人通道仍在' : '外部机器人已切断'),
          h(Pill, { tone: archiverUp ? 'ok' : 'idle' }, archiverUp ? '归档器运行中' : '归档器未运行'),
          // 归档器在跑、但保存目录写不进去时，单看「运行中」会产生「那就能收文件了」的误解。
          launcher && launcher.saveRootOk === false
            ? h(Pill, { tone: 'bad' }, '保存目录不可写')
            : null,
        ),

        // 第三行：程序目录 + 单独启动（原 LauncherCard 合并到这里）
        //
        // 这里有独立启动按钮不是冗余：归档模式关着的时候用户也可能只想让归档器
        // 挂日常号收文件。区别只在于「要不要切断外部机器人 + 切号」，所以按钮
        // 文案和提示都写清楚，避免和上面的开关搞混。
        h(
          'div',
          {
            style: {
              display: 'flex',
              gap: 10,
              alignItems: 'center',
              flexWrap: 'wrap',
              paddingTop: 2,
              borderTop: '1px solid ' + C.border1,
              marginTop: 2,
              padding: '10px 0 0',
            },
          },
          h(
            'span',
            { style: { ...S.mono, fontSize: 11, color: C.label3, flex: '1 1 240px', wordBreak: 'break-all', minWidth: 0 } },
            launcher && launcher.appDir ? launcher.appDir : '没找到归档器程序目录',
          ),
          h(
            'label',
            {
              style: {
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                cursor: 'pointer',
                fontSize: 11,
                color: admin ? C.label1 : C.label3,
                padding: '3px 9px',
                borderRadius: 7,
                border: '1px solid ' + (admin ? C.brand : C.border2),
                background: admin ? C.bg3 : 'transparent',
                flex: 'none',
              },
              title: '默认用当前权限启动就够了；只有归档器报告「保存目录不可写」时才会自动用管理员权限重试一次。勾上这里可以强制走管理员（注意：UAC 确认框可能弹在安全桌面上，看起来像没反应）。',
            },
            h('span', { style: { color: admin ? C.brand : C.label3, display: 'flex' } }, Ico.shield({ size: 12 })),
            h('input', {
              type: 'checkbox',
              checked: !!admin,
              onChange: (e) => setAdmin(e.target.checked),
              style: { margin: 0, cursor: 'pointer' },
            }),
            '管理员启动',
          ),
          h(
            Btn,
            {
              small: true,
              disabled: launching || archiverUp || !batOk,
              onClick: () => onLaunch(admin),
              icon: Ico.play({ size: 12 }),
              title: archiverUp
                ? '归档器已在运行；要停止请关掉它的控制台窗口'
                : batOk
                  ? '只启动归档器，不切号、不切断外部机器人'
                  : '找不到 启动.bat，请检查设置里的「程序目录」',
            },
            launching ? '启动中…' : archiverUp ? '已在运行' : '只启动归档器',
          ),
        ),
        !batOk && launcher && launcher.appDir
          ? h('div', { style: { fontSize: 11, color: C.warn } }, '这个目录里找不到「启动.bat」，单独启动按钮不可用。')
          : null,

        // 第四行：一句话说明它到底做了什么
        h(
          'div',
          { style: { fontSize: 11, color: C.label3, lineHeight: 1.6 } },
          '开启时会做四件事：① 把 ' + grabUin + ' 的 OneBot 客户端改成只连归档器 ' +
            ((state && state.wsPort) || 6200) + '（外部机器人物理上连不进来，所以不会代它说话）；' +
            '② 让 NapCat 以 ' + grabUin + ' 重登；③ 启动归档器（默认用当前权限，写不进保存目录时才自动提权重试一次）；' +
            '④ 记下原配置，供关闭时还原。',
        ),

        // 第五行：逐条步骤结果（切换时实时追加，跑完保留结果）
        steps.length > 0 || swRunning || failed
          ? h(
              'div',
              {
                style: {
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                  background: C.bg2,
                  border: '1px solid ' + (failed ? C.error : C.border1),
                  borderRadius: 8,
                  padding: '9px 11px',
                },
              },
              swRunning
                ? h(
                    'div',
                    { style: { display: 'flex', gap: 8, alignItems: 'center', fontSize: 11, color: C.brand } },
                    h('span', { style: { display: 'flex' } }, Ico.refresh({ size: 12 })),
                    h('span', null, '正在切换…（重启 NapCat 要十几秒，别关页面）'),
                  )
                : null,
              failed && result && result.error
                ? h(
                    'div',
                    { style: { display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 11, color: C.error } },
                    h('span', { style: { display: 'flex', marginTop: 1 } }, Ico.alert({ size: 12 })),
                    h('span', { style: { minWidth: 0 } }, result.error),
                  )
                : null,
              steps.map((s, i) =>
                h(
                  'div',
                  { key: i, style: { display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 11 } },
                  h('span', { style: { color: s.ok ? C.success : C.error, display: 'flex', marginTop: 1 } },
                    s.ok ? Ico.check({ size: 12 }) : Ico.alert({ size: 12 })),
                  h('span', { style: { color: C.label2, whiteSpace: 'nowrap' } }, s.name),
                  h('span', { style: { color: C.label3, minWidth: 0 } }, s.detail || ''),
                ),
              ),
            )
          : null,
      )
    }

    // ---------- 概览页 ----------

    function OverviewTab(props) {
      const {
        data, loading, error, onRefresh, launcher, launching, onLaunch, admin, setAdmin,
        sw, swLoading, swBusy, swResult, onSwitch,
      } = props

      const modeCard = () =>
        h(ArchiveModeCard, {
          state: sw,
          loading: swLoading,
          busy: swBusy,
          result: swResult,
          onToggle: onSwitch,
          launcher,
          launching,
          onLaunch,
          admin,
          setAdmin,
        })

      if (error) {
        return h(
          'div',
          { style: S.stack },
          modeCard(),
          h(
            'div',
            {
              style: {
                ...S.card,
                borderColor: C.error,
                color: C.error,
                display: 'flex',
                gap: 10,
                alignItems: 'flex-start',
              },
            },
            h('span', { style: { display: 'flex', marginTop: 1 } }, Ico.alert({ size: 16 })),
            h('div', null, error, h('div', { style: { ...S.muted, marginTop: 6 } }, '用上面的开关启动归档器后再回来刷新。')),
          ),
        )
      }
      if (loading && !data) return h(Loading, null)
      if (!data) return null

      const st = data.status || {}
      const stats = data.stats || {}
      const dl = stats.downloader || {}
      const groups = Array.isArray(stats.by_group) ? stats.by_group : []
      const cats = Array.isArray(stats.by_category) ? stats.by_category : []
      const accounts = Array.isArray(st.accounts) ? st.accounts : []
      const c = st.counters || {}

      const maxCat = cats.reduce((m, x) => Math.max(m, Number(x.s) || 0), 0) || 1

      return h(
        'div',
        { style: S.stack },

        modeCard(),

        !st.save_root_ok
          ? h(
              'div',
              {
                style: {
                  ...S.card,
                  borderColor: C.error,
                  color: C.error,
                  display: 'flex',
                  gap: 10,
                  alignItems: 'flex-start',
                },
              },
              h('span', { style: { display: 'flex', marginTop: 1 } }, Ico.alert({ size: 16 })),
              h(
                'div',
                null,
                '保存目录不可写：' + (st.save_root || ''),
                h(
                  'div',
                  { style: { ...S.muted, marginTop: 5 } },
                  '当前进程可能继承了受限沙箱（或被安全软件拦住）。用上面的「只启动归档器」勾选「管理员启动」重新拉一次即可。',
                ),
              ),
            )
          : null,

        // 指标卡：用真网格，列数随宽度自动增减，不会最后一行只剩一个孤零零的。
        h(
          'div',
          { style: S.statGrid },
          h(Stat, {
            label: 'NapCat 连接',
            value: st.onebot_connected ? '已连接' : '未连接',
            hint: (st.account_count || 0) + ' 个账号在线',
            tone: st.onebot_connected ? 'ok' : 'bad',
            icon: Ico.users({ size: 13 }),
          }),
          h(Stat, {
            label: '累计入库',
            value: String(stats.total_files || 0),
            hint: fmtSize(stats.total_size) + ' · 个文件',
            icon: Ico.archive({ size: 13 }),
          }),
          h(Stat, {
            label: '待下载',
            value: String(dl.pending || 0),
            hint: '进行中 ' + (dl.active || 0) + ' · 失败 ' + (dl.failed || 0) +
              (dl.gone ? ' · 已无 ' + dl.gone : ''),
            tone: dl.failed ? 'warn' : 'brand',
            icon: Ico.download({ size: 13 }),
          }),
          h(Stat, {
            label: '实时事件',
            value: String((c.group_upload || 0) + (c.chat_video || 0) + (c.chat_file || 0)),
            hint: '群文件 ' + (c.group_upload || 0) + ' · 视频 ' + (c.chat_video || 0),
            icon: Ico.clock({ size: 13 }),
          }),
        ),

        h(
          'div',
          { style: S.twoCol },

          h(
            Card,
            { title: '登录账号' },
            accounts.length === 0
              ? h(Empty, {
                  text: '还没有账号连上来',
                  hint: '确认 NapCat 已登录，且 OneBot 的 WebSocket 客户端指向 ws://127.0.0.1:' + (st.ws_port || 6200) + '/ws',
                })
              : h(
                  'div',
                  { style: { display: 'flex', flexDirection: 'column', gap: 8 } },
                  accounts.map((a) =>
                    h(
                      'div',
                      {
                        key: a.self_id,
                        style: {
                          display: 'flex',
                          gap: 9,
                          alignItems: 'center',
                          padding: '8px 10px',
                          borderRadius: 8,
                          background: C.bg3,
                        },
                      },
                      h(Dot, { tone: a.alive ? 'ok' : 'bad' }),
                      h('span', { style: { ...S.mono, fontWeight: 600 } }, a.self_id),
                      a.nickname ? h('span', { style: S.muted }, a.nickname) : null,
                      h('span', { style: { ...S.muted, marginLeft: 'auto', fontSize: 11 } }, a.addr || ''),
                    ),
                  ),
                ),
          ),

          h(
            Card,
            { title: '按类型' },
            cats.length === 0
              ? h(Empty, { text: '暂无数据' })
              : h(
                  'div',
                  { style: { display: 'flex', flexDirection: 'column', gap: 8 } },
                  cats.map((x) =>
                    h(
                      'div',
                      { key: x.category, style: { display: 'flex', flexDirection: 'column', gap: 3 } },
                      h(
                        'div',
                        { style: { display: 'flex', justifyContent: 'space-between', fontSize: 12 } },
                        h('span', null, x.category || '其他'),
                        h('span', { style: { ...S.muted, ...S.mono } }, x.n + ' · ' + fmtSize(x.s)),
                      ),
                      h(Bar, { pct: ((Number(x.s) || 0) / maxCat) * 100 }),
                    ),
                  ),
                ),
          ),
        ),

        h(
          Card,
          { title: '按群', extra: h('span', { style: S.muted }, '共 ' + groups.length + ' 个群有归档') },
          groups.length === 0
            ? h(Empty, { text: '暂无数据' })
            : h(
                'div',
                { style: { display: 'flex', flexDirection: 'column' } },
                groups.slice(0, 10).map((g) =>
                  h(
                    'div',
                    {
                      key: g.group_id,
                      style: {
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                        padding: '7px 2px',
                        borderBottom: '1px solid ' + C.border1,
                      },
                    },
                    h('span', { style: { flex: 'none', color: C.label3, display: 'flex' } }, Ico.users({ size: 13 })),
                    h(
                      'span',
                      { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 } },
                      g.group_name || g.group_id,
                    ),
                    h('span', { style: { ...S.muted, ...S.mono, flex: 'none' } }, g.n + ' 个'),
                    h('span', { style: { ...S.muted, ...S.mono, flex: 'none', minWidth: 62, textAlign: 'right' } }, fmtSize(g.s)),
                  ),
                ),
              ),
        ),

        h(
          'div',
          { style: { ...S.muted, display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', paddingTop: 2 } },
          h('span', null, 'WS ' + (st.ws_port || '-') + ' · Web ' + (st.web_port || '-')),
          h('span', { style: { display: 'flex', alignItems: 'center', gap: 5 } }, Ico.folder({ size: 12 }), st.save_root || '-'),
          h('span', null, '面板刷新于 ' + new Date().toLocaleTimeString()),
          h(
            Btn,
            { variant: 'ghost', small: true, onClick: onRefresh, icon: Ico.refresh({ size: 12 }) },
            '刷新',
          ),
        ),
      )
    }

    // ---------- 群列表页 ----------

    function GroupsTab(props) {
      const { data, loading, error, onToggle, busyId, onRefreshGroups, msg } = props
      if (error) return h('div', { style: S.err }, error)
      if (loading && !data) return h(Loading, null)
      const groups = data && Array.isArray(data.groups) ? data.groups : []
      const on = groups.filter((g) => g.enabled).length

      return h(
        'div',
        { style: S.stack },
        h(
          'div',
          { style: { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' } },
          h(Pill, { tone: on > 0 ? 'ok' : undefined }, on + ' / ' + groups.length + ' 在抓取'),
          h('span', { style: S.muted }, '勾选左侧方框即可开关单个群'),
          h(
            'div',
            { style: { marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' } },
            msg ? h('span', { style: S.ok }, msg) : null,
            h(
              Btn,
              { onClick: onRefreshGroups, icon: Ico.refresh({ size: 13 }) },
              '从 NapCat 刷新',
            ),
          ),
        ),
        groups.length === 0
          ? h(Empty, { text: '还没有任何群', hint: '先点右上角「从 NapCat 刷新」，前提是 NapCat 已连上' })
          : h(
              'div',
              { style: { ...S.card, padding: 0, overflow: 'hidden' } },
              groups.map((g, i) =>
                h(
                  'div',
                  {
                    key: g.group_id,
                    style: {
                      ...S.row,
                      alignItems: 'center',
                      // 最后一行不再画分隔线，否则卡片底边会出现双重线
                      borderBottom: i === groups.length - 1 ? 'none' : '1px solid ' + C.border1,
                      background: g.enabled ? 'transparent' : C.bg2,
                    },
                  },
                  // 勾选框放最左，回车/空格都能切；禁用的那一刻连光标都变成等待
                  h('input', {
                    type: 'checkbox',
                    checked: !!g.enabled,
                    disabled: busyId === g.group_id,
                    onChange: (e) => onToggle(g.group_id, e.target.checked),
                    title: g.enabled ? '取消抓取这个群' : '开始抓取这个群',
                    style: { cursor: busyId === g.group_id ? 'wait' : 'pointer', margin: 0, flex: 'none' },
                  }),
                  h(
                    'div',
                    { style: S.rowMain },
                    // 群名是这一行的主信息，长名字省略而不是撑破容器
                    h('div', { style: S.rowTitle, title: g.group_name || '' }, g.group_name || '-'),
                    h(
                      'div',
                      { style: S.rowMeta },
                      h('span', { style: S.mono }, String(g.group_id)),
                      h('span', null, '·'),
                      h('span', null, (g.member_count || '-') + ' 人'),
                      h('span', null, '·'),
                      h('span', null, '已下 ' + (g.file_count || 0)),
                      h('span', null, '·'),
                      h('span', null, g.last_scan ? fmtAgo(g.last_scan) : '未扫描'),
                    ),
                  ),
                  h(
                    'div',
                    { style: S.rowSide },
                    h(Pill, { tone: g.enabled ? 'ok' : undefined }, g.enabled ? '抓取中' : '已关闭'),
                  ),
                ),
              ),
            ),
      )
    }

    // ---------- 文件页 ----------

    const CATEGORIES = ['', '视频', '文档', '表格', '幻灯片', '压缩包', '图片', '音频', '软件', '代码', '其他']
    const CAT_ICON = {
      视频: Ico.play,
      文档: Ico.doc,
      表格: Ico.sheet,
      幻灯片: Ico.slide,
      压缩包: Ico.zip,
      图片: Ico.image,
      音频: Ico.music,
      软件: Ico.app,
      代码: Ico.code,
      其他: Ico.tag,
    }
    const catIcon = (c) => CAT_ICON[c] || Ico.archive

    /** 分组/平铺切换按钮的样式：选中态用品牌底色。 */
    function viewBtnStyle(active) {
      return {
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '4px 9px',
        borderRadius: 6,
        border: 'none',
        cursor: 'pointer',
        fontFamily: 'inherit',
        fontSize: 11,
        fontWeight: 600,
        background: active ? C.brand : 'transparent',
        color: active ? C.onBrand : C.label2,
        transition: 'background .15s ease, color .15s ease',
      }
    }

    /** 一行文件。平铺与分组两种视图共用，避免两套渲染走样。 */
    function FileRow(props) {
      const { f, i, baseUrl, onOpenFolder } = props
      const isMedia = ['视频', '音频', '图片'].indexOf(f.category) >= 0
      const I = catIcon(f.category)
      return h(
        'div',
        {
          style: {
            ...S.row,
            alignItems: 'center',
            // 最后一行不画分隔线，否则卡片底边会出现双重线
            borderBottom: props.last ? 'none' : '1px solid ' + C.border1,
            background: i % 2 ? C.bg2 : 'transparent',
          },
        },
        // 类别图标是这一行的视觉锚点，比在表格里占一整列省地方得多
        h('span', { style: { display: 'flex', color: C.brand, flex: 'none', marginTop: 1 } }, I({ size: 14 })),
        h(
          'div',
          { style: S.rowMain },
          h('div', { style: S.rowTitle, title: f.file_name || '' }, f.file_name || '-'),
          h(
            'div',
            { style: S.rowMeta },
            h('span', null, f.category || '-'),
            h('span', null, '·'),
            h('span', { style: S.mono }, fmtSize(f.size)),
            h('span', null, '·'),
            h('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, f.group_name || String(f.group_id || '')),
            h('span', null, '·'),
            h('span', null, fmtTime(f.created_at)),
          ),
        ),
        h(
          'div',
          { style: S.rowSide },
          isMedia && baseUrl
            ? h(
                'a',
                {
                  href: baseUrl + '/media/' + f.id,
                  target: '_blank',
                  rel: 'noreferrer',
                  title: '在新标签打开',
                  style: { ...S.link, display: 'inline-flex', alignItems: 'center', gap: 3 },
                },
                h('span', { style: { display: 'flex' } }, Ico.play({ size: 12 })),
              )
            : null,
          h(
            Btn,
            { small: true, variant: 'ghost', onClick: () => onOpenFolder(f.id), icon: Ico.folder({ size: 12 }) },
            '定位',
          ),
        ),
      )
    }

    /** 平铺视图：一行一个文件，和分组视图共用同一个 FileRow。 */
    function FileTable(props) {
      const { rows, baseUrl, onOpenFolder, bare } = props
      return h(
        'div',
        {
          style: bare
            ? { overflow: 'hidden' }
            : { ...S.card, padding: 0, overflow: 'hidden' },
        },
        rows.map((f, i) =>
          h(FileRow, {
            key: f.id,
            f,
            i,
            baseUrl,
            onOpenFolder,
            // 最后一行去掉分隔线
            last: i === rows.length - 1,
          }),
        ),
      )
    }

    /** 按类别折叠分组。默认只有第一组展开，避免一次性铺满整屏。 */
    function GroupedFiles(props) {
      const { rows, baseUrl, onOpenFolder } = props
      const [open, setOpen] = React.useState({})
      const groups = React.useMemo(() => {
        const map = new Map()
        for (const f of rows) {
          const k = f.category || '其他'
          if (!map.has(k)) map.set(k, [])
          map.get(k).push(f)
        }
        // 顺序按 CATEGORIES 排，未知类别落最后，保证每次刷新位置稳定
        const order = (c) => {
          const i = CATEGORIES.indexOf(c)
          return i < 0 ? 99 : i
        }
        return [...map.entries()].sort((a, b) => order(a[0]) - order(b[0]))
      }, [rows])

      return h(
        'div',
        { style: S.stack },
        groups.map(([cat, list], gi) => {
          // open 里没有这个键时用「第一组默认展开」兜底
          const isOpen = open[cat] === undefined ? gi === 0 : open[cat]
          const size = list.reduce((s, f) => s + (Number(f.size) || 0), 0)
          const I = catIcon(cat)
          return h(
            'div',
            { key: cat, style: { border: '1px solid ' + C.border1, borderRadius: 10, overflow: 'hidden', background: C.bg2 } },
            h(
              'button',
              {
                type: 'button',
                onClick: () => setOpen({ ...open, [cat]: !isOpen }),
                style: {
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  width: '100%',
                  padding: '9px 12px',
                  border: 'none',
                  background: 'transparent',
                  color: C.label1,
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                  fontSize: 13,
                  textAlign: 'left',
                },
              },
              h(
                'span',
                {
                  style: {
                    display: 'flex',
                    color: C.label3,
                    transform: isOpen ? 'rotate(90deg)' : 'none',
                    transition: 'transform .15s ease',
                  },
                },
                Ico.chevron({ size: 12 }),
              ),
              h('span', { style: { display: 'flex', color: C.brand } }, I({ size: 14 })),
              h('span', { style: { fontWeight: 600 } }, cat),
              h(Pill, null, list.length + ' 个'),
              h('span', { style: { ...S.muted, ...S.mono, marginLeft: 'auto' } }, fmtSize(size)),
            ),
            isOpen
              ? h(
                  'div',
                  { style: { borderTop: '1px solid ' + C.border1, background: C.bg1 } },
                  // bare：已经在外层卡片里了，别再套一层边框，否则会出现双线方框
                  h(FileTable, { rows: list, baseUrl, onOpenFolder, bare: true }),
                )
              : null,
          )
        }),
      )
    }

    function FilesTab(props) {
      const { data, loading, error, query, setQuery, onPage, onOpenFolder, baseUrl } = props
      const files = data && Array.isArray(data.files) ? data.files : []
      const total = (data && data.total) || 0
      const limit = (data && data.limit) || 40
      const offset = (data && data.offset) || 0

      // 视图选择记在 localStorage 里，切 Tab 回来不会跳回默认
      const [grouped, setGrouped] = React.useState(() => {
        try {
          return localStorage.getItem('qqark.filesGrouped') !== '0'
        } catch {
          return true
        }
      })
      const switchView = (next) => {
        setGrouped(next)
        try {
          localStorage.setItem('qqark.filesGrouped', next ? '1' : '0')
        } catch {
          /* 隐私模式下存不了就只影响记忆，不影响本次切换 */
        }
      }

      return h(
        'div',
        { style: S.stack },
        h(
          'div',
          { style: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' } },
          h(
            'div',
            { style: { position: 'relative', display: 'flex', alignItems: 'center' } },
            h(
              'span',
              { style: { position: 'absolute', left: 9, color: C.label3, display: 'flex', pointerEvents: 'none' } },
              Ico.search({ size: 13 }),
            ),
            h('input', {
              style: { ...S.input, width: 220, paddingLeft: 28 },
              placeholder: '搜文件名…',
              value: query.q || '',
              onChange: (e) => setQuery({ ...query, q: e.target.value, offset: 0 }),
            }),
          ),
          h(
            'select',
            {
              style: { ...S.input, cursor: 'pointer' },
              value: query.category || '',
              onChange: (e) => setQuery({ ...query, category: e.target.value, offset: 0 }),
            },
            CATEGORIES.map((c) => h('option', { key: c, value: c }, c || '全部类型')),
          ),
          h(Pill, { tone: total > 0 ? 'ok' : undefined }, '共 ' + total + ' 条'),
          // 分组 / 平铺切换。分组是默认视图，因为「分门别类」正是这个页面的目的。
          h(
            'div',
            { style: { display: 'flex', gap: 2, marginLeft: 'auto', padding: 2, borderRadius: 8, background: C.bg3 } },
            h(
              'button',
              {
                type: 'button',
                onClick: () => switchView(true),
                title: '按类别分组',
                style: viewBtnStyle(grouped),
              },
              h('span', { style: { display: 'flex' } }, Ico.grid({ size: 13 })),
              '分组',
            ),
            h(
              'button',
              {
                type: 'button',
                onClick: () => switchView(false),
                title: '平铺表格',
                style: viewBtnStyle(!grouped),
              },
              h('span', { style: { display: 'flex' } }, Ico.list({ size: 13 })),
              '平铺',
            ),
          ),
        ),
        error ? h('div', { style: S.err }, error) : null,
        loading && files.length === 0 ? h(Loading, null) : null,
        files.length === 0
          ? h(Empty, { text: '没有匹配的文件', hint: total === 0 ? '归档器里还没有下载记录' : '换个关键词试试' })
          : grouped
            ? h(GroupedFiles, { rows: files, baseUrl, onOpenFolder })
            : h(FileTable, { rows: files, baseUrl, onOpenFolder }),
        h(
          'div',
          { style: { display: 'flex', gap: 8, alignItems: 'center' } },
          h(
            Btn,
            { small: true, disabled: offset <= 0, onClick: () => onPage(Math.max(0, offset - limit)) },
            '上一页',
          ),
          h('span', { style: S.muted }, (total === 0 ? 0 : offset + 1) + '–' + Math.min(offset + files.length, total) + ' / ' + total),
          h(
            Btn,
            { small: true, disabled: offset + files.length >= total, onClick: () => onPage(offset + limit) },
            '下一页',
          ),
        ),
      )
    }

    // ---------- 任务页 ----------

    /**
     * 任务状态的中文名与配色。
     *
     * `gone` 是 0.5.2 新增的终态：群里已经确认没有这个文件了（QQ 群文件有
     * 服务端保留期，老文件的记录还在列表里但文件体已被清理）。它必须和普通
     * `failed` 分开显示，否则用户会一直去点「重试全部失败」，而那个按钮现在
     * 已经不重试 gone 了 —— 看不到区别就会以为是按钮坏了。
     */
    const TASK_STATE = {
      pending: { text: '待下载', tone: undefined, dot: 'idle' },
      running: { text: '下载中', tone: 'ok', dot: 'ok' },
      done: { text: '已完成', tone: 'ok', dot: 'ok' },
      skipped: { text: '已跳过', tone: undefined, dot: 'idle' },
      gone: { text: '群里已无', tone: 'warn', dot: 'idle' },
      failed: { text: '失败', tone: 'bad', dot: 'bad' },
    }
    const taskMeta = (state) => TASK_STATE[state] || { text: state || '-', tone: undefined, dot: 'idle' }

    function TasksTab(props) {
      const { data, loading, error, onScan, onRetry, msg } = props
      if (error) return h('div', { style: S.err }, error)
      if (loading && !data) return h(Loading, null)
      const dl = (data && data.downloader) || {}
      const sc = (data && data.scanner) || {}
      const tasks = data && Array.isArray(data.tasks) ? data.tasks : []

      return h(
        'div',
        { style: S.stack },
        h(
          'div',
          { style: S.statGrid },
          h(Stat, { label: '待下载', value: String(dl.pending || 0), hint: '进行中 ' + (dl.active || 0), icon: Ico.download({ size: 13 }) }),
          h(Stat, { label: '已完成', value: String(dl.done || 0), tone: 'ok', icon: Ico.check({ size: 13 }) }),
          h(Stat, { label: '失败', value: String(dl.failed || 0), tone: dl.failed ? 'bad' : 'brand', icon: Ico.alert({ size: 13 }) }),
          dl.gone
            ? h(Stat, { label: '群里已无', value: String(dl.gone), hint: '过期清理，下不来了', tone: 'warn', icon: Ico.alert({ size: 13 }) })
            : null,
        ),
        h(
          'div',
          { style: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' } },
          h(Btn, { variant: 'primary', onClick: onScan, icon: Ico.refresh({ size: 13 }) }, '立即补扫'),
          h(Btn, { onClick: onRetry }, '重试全部失败'),
          h(
            'span',
            { style: { ...S.muted, display: 'flex', alignItems: 'center', gap: 6 } },
            h(Dot, { tone: sc.running ? 'ok' : 'bad' }),
            '补扫器 ' + (sc.running ? '运行中' : '空闲') +
              ' · 已扫 ' + (sc.scanned || 0) + ' 个目录 · 入队 ' + (sc.queued || 0) +
              ' · 上次 ' + fmtAgo(sc.last_run),
          ),
          msg ? h('span', { style: S.ok }, msg) : null,
        ),
        sc.error
          ? h(
              'div',
              { style: { ...S.card, borderColor: C.error, color: C.error, fontSize: 12 } },
              '补扫异常：' + sc.error,
            )
          : null,
        tasks.length === 0
          ? h(Empty, { text: '还没有任务记录' })
          : h(
              // 和文件页一样用列表行：五列表格在 458px 里余量为 0，文件名一长就溢出。
              'div',
              { style: { ...S.card, padding: 0, overflow: 'hidden' } },
              tasks.slice(0, 60).map((t, i) =>
                h(
                  'div',
                  {
                    key: (t.id || '') + '-' + i,
                    style: {
                      ...S.row,
                      alignItems: 'center',
                      borderBottom: i === Math.min(tasks.length, 60) - 1 ? 'none' : '1px solid ' + C.border1,
                      background: i % 2 ? C.bg2 : 'transparent',
                    },
                  },
                  h('div', { style: { flex: 'none', paddingTop: 1 } }, h(Dot, { tone: taskMeta(t.state).dot })),
                  h(
                    'div',
                    { style: S.rowMain },
                    h('div', { style: S.rowTitle, title: t.file_name || '' }, t.file_name || '-'),
                    h(
                      'div',
                      { style: S.rowMeta },
                      h(Pill, { tone: taskMeta(t.state).tone }, taskMeta(t.state).text),
                      h('span', { style: S.mono }, fmtSize(t.size)),
                      t.attempts ? h('span', null, '重试 ' + t.attempts + ' 次') : null,
                    ),
                    // 失败原因单独一行并允许折两行：它是排查时唯一想看清的字段。
                    // gone 用 warn 而不是 error —— 它不是"出了问题"，是"这文件本来就没了"。
                    t.last_error
                      ? h(
                          'div',
                          {
                            title: t.last_error,
                            style: {
                              fontSize: 11,
                              color: t.state === 'gone' ? C.warn : C.error,
                              lineHeight: 1.5,
                              overflow: 'hidden',
                              display: '-webkit-box',
                              WebkitLineClamp: 2,
                              WebkitBoxOrient: 'vertical',
                              wordBreak: 'break-all',
                            },
                          },
                          t.last_error,
                        )
                      : null,
                  ),
                  t.state === 'running' && typeof t.progress === 'number'
                    ? h('div', { style: { ...S.rowSide, minWidth: 54 } },
                        h(Bar, { pct: t.progress }),
                        h('div', { style: { ...S.mono, fontSize: 10, color: C.label3, textAlign: 'right', marginTop: 3 } }, t.progress + '%'))
                    : null,
                ),
              ),
            ),
      )
    }

    // ---------- 日志页 ----------

    /** 级别配色：ERROR 红、WARN 黄、其余灰。 */
    function levelColor(level) {
      if (level === 'ERROR' || level === 'CRITICAL') return C.error
      if (level === 'WARN') return C.warn
      if (level === 'DEBUG') return C.label3
      return C.label2
    }

    function LogsTab(props) {
      const { data, loading, error, view, setView, onRefresh, onOpenDir } = props
      const archiver = (data && Array.isArray(data.archiver)) ? data.archiver : []
      const plugin = (data && Array.isArray(data.plugin)) ? data.plugin : []
      const errors = (data && Array.isArray(data.errors)) ? data.errors : []
      const sizes = (data && data.sizes) || {}

      // 三个视图其实只是在同一个数组上做不同筛选，所以放这里算一次
      const lines =
        view === 'problems'
          ? errors
          : view === 'plugin'
            ? plugin
            : archiver

      const VIEWS = [
        ['archiver', '归档器', archiver.length],
        ['plugin', '插件动作', plugin.length],
        ['problems', '只看问题', errors.length],
      ]

      return h(
        'div',
        { style: S.stack },
        h(
          'div',
          { style: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' } },
          VIEWS.map(([k, l, n]) =>
            h(
              'button',
              {
                key: k,
                type: 'button',
                onClick: () => setView(k),
                style: {
                  padding: '5px 12px',
                  borderRadius: 8,
                  cursor: 'pointer',
                  // 选中的用「淡底 + 品牌色描边 + 品牌色文字」，不铺实心色块：
                  // 和上面那条下划线式标签栏一样，避免两块实心品牌色互抢注意力。
                  border: '1px solid ' + (view === k ? C.brand : C.border2),
                  background: view === k ? C.bg3 : 'transparent',
                  color: view === k ? C.brand : C.label2,
                  fontSize: 12,
                  fontWeight: view === k ? 600 : 500,
                  fontFamily: 'inherit',
                  whiteSpace: 'nowrap',
                  transition: 'color .15s ease, border-color .15s ease, background .15s ease',
                },
              },
              l + (n ? ' · ' + n : ''),
            ),
          ),
          h(
            'div',
            { style: { marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' } },
            h(
              'span',
              { style: { ...S.muted, ...S.mono } },
              fmtSize(sizes.archiver || 0) + ' / ' + fmtSize(sizes.plugin || 0),
            ),
            h(Btn, { small: true, onClick: onRefresh, icon: Ico.refresh({ size: 12 }) }, '刷新'),
            h(Btn, { small: true, variant: 'ghost', onClick: onOpenDir, icon: Ico.folder({ size: 12 }) }, '打开目录'),
          ),
        ),
        error ? h('div', { style: S.err }, error) : null,
        loading && lines.length === 0 ? h(Loading, null) : null,
        lines.length === 0
          ? h(Empty, {
              text:
                view === 'problems'
                  ? '没有警告或错误，状态很干净'
                  : view === 'plugin'
                    ? '插件还没记录过动作日志（点一次开启就会写入）'
                    : '归档器还没有日志',
              hint:
                view === 'archiver'
                  ? '归档器启动后才会写 data/qqark.log'
                  : '日志位置：程序目录\\data\\',
            })
          : h(
              'div',
              {
                style: {
                  ...S.card,
                  padding: '8px 0',
                  maxHeight: 'calc(100vh - 300px)',
                  overflow: 'auto',
                },
              },
              lines.map((l, i) =>
                h(
                  'div',
                  {
                    key: i,
                    style: {
                      display: 'flex',
                      gap: 8,
                      alignItems: 'flex-start',
                      padding: '3px 12px',
                      fontSize: 11,
                      lineHeight: 1.55,
                      fontFamily: C.mono,
                      background: l.level === 'ERROR' ? 'rgba(220,60,60,.07)' : 'transparent',
                    },
                  },
                  h(
                    'span',
                    { style: { color: C.label3, flex: 'none', whiteSpace: 'nowrap' } },
                    String(l.t || '').replace('T', ' ').slice(5, 19),
                  ),
                  h(
                    'span',
                    { style: { color: levelColor(l.level), flex: 'none', width: 42, fontWeight: 700 } },
                    l.level === 'CRITICAL' ? 'CRIT' : l.level,
                  ),
                  l.from
                    ? h('span', { style: { color: C.brand, flex: 'none' } }, '[' + l.from + ']')
                    : null,
                  l.src && !l.from
                    ? h('span', { style: { color: C.label3, flex: 'none' } }, '[' + l.src + ']')
                    : null,
                  h(
                    'span',
                    { style: { color: C.label2, minWidth: 0, wordBreak: 'break-word', whiteSpace: 'pre-wrap' } },
                    l.msg || '',
                  ),
                ),
              ),
            ),
        h(
          'div',
          { style: { ...S.muted, fontSize: 11, lineHeight: 1.6 } },
          '归档器日志：程序目录\\data\\qqark.log · 插件动作日志：程序目录\\data\\qqark-plugin.log。',
          '「只看问题」把两边的警告和错误按时间倒序合在一起，排查时看这个就够。',
        ),
      )
    }

    // ---------- 设置页 ----------

    function SettingsTab(props) {
      const { form, setForm, onSave, saving, msg, error, launcher } = props
      if (!form) return h(Loading, null)

      const row = (label, hint, control) =>
        h(
          'div',
          { style: { display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 14 } },
          h('div', { style: { fontSize: 12, fontWeight: 600 } }, label),
          control,
          hint ? h('div', { style: S.muted }, hint) : null,
        )

      const check = (key, label, hint) =>
        h(
          'label',
          {
            style: {
              display: 'flex',
              alignItems: 'flex-start',
              gap: 9,
              marginBottom: 12,
              cursor: 'pointer',
              padding: '8px 10px',
              borderRadius: 8,
              background: C.bg3,
            },
          },
          h('input', {
            type: 'checkbox',
            checked: !!form[key],
            onChange: (e) => setForm({ ...form, [key]: e.target.checked }),
            style: { marginTop: 2, cursor: 'pointer' },
          }),
          h(
            'span',
            null,
            h('div', { style: { fontSize: 12, fontWeight: 500 } }, label),
            hint ? h('div', { style: S.muted }, hint) : null,
          ),
        )

      const num = (key, label, hint) =>
        row(
          label,
          hint,
          h('input', {
            type: 'number',
            style: { ...S.input, width: 150, ...S.mono },
            value: form[key] === undefined || form[key] === null ? '' : form[key],
            onChange: (e) => setForm({ ...form, [key]: Number(e.target.value) }),
          }),
        )

      return h(
        'div',
        { style: S.stack },

        h(
          Card,
          { title: '归档位置与范围' },
          h(
            'div',
            { style: { maxWidth: 640 } },
            row(
              '保存位置',
              '下载下来的文件按「群名_群号 / 年-月 / 类别 / 文件名」分门别类存放。',
              h('input', {
                style: { ...S.input, width: '100%' },
                value: form.save_root || '',
                onChange: (e) => setForm({ ...form, save_root: e.target.value }),
              }),
            ),
            row(
              '监听群号',
              '留空 = 抓取全部已启用的群。多个群号用逗号或空格分隔。',
              h('input', {
                style: { ...S.input, width: '100%' },
                placeholder: '例如 123456789,987654321',
                value: Array.isArray(form.watch_groups) ? form.watch_groups.join(',') : form.watch_groups || '',
                onChange: (e) => setForm({ ...form, watch_groups: e.target.value }),
              }),
            ),
            row(
              '目录结构',
              '默认按群/年月/类型三级分目录。',
              h(
                'select',
                {
                  style: { ...S.input, cursor: 'pointer' },
                  value: form.layout || 'group_date_type',
                  onChange: (e) => setForm({ ...form, layout: e.target.value }),
                },
                h('option', { value: 'group_date_type' }, '群 / 年月 / 类型'),
                h('option', { value: 'group_type' }, '群 / 类型'),
                h('option', { value: 'date_type' }, '年月 / 类型'),
                h('option', { value: 'type_only' }, '只按类型'),
              ),
            ),
          ),
        ),

        h(
          Card,
          { title: '抓取内容' },
          h(
            'div',
            { style: { display: 'flex', flexWrap: 'wrap', gap: '0 24px' } },
            h('div', { style: { flex: '1 1 260px' } },
              check('grab_group_files', '群文件', '群里「文件」页签下的所有类型'),
              check('grab_chat_video', '聊天视频', '聊天里直接发的视频'),
              check('grab_chat_file', '聊天文件', '以文件形式发出的附件'),
            ),
            h('div', { style: { flex: '1 1 260px' } },
              check('grab_images', '聊天图片', '默认关闭'),
              check('grab_audio', '聊天语音', '默认关闭'),
              check('scan_enabled', '启用补扫', '定期扫描群文件列表，补上实时事件漏掉的'),
            ),
          ),
        ),

        h(
          Card,
          { title: '性能与过滤' },
          h(
            'div',
            { style: { display: 'flex', flexWrap: 'wrap', gap: '0 28px', maxWidth: 760 } },
            h('div', { style: { flex: '1 1 220px' } },
              num('scan_interval_min', '补扫间隔（分钟）', '默认 30'),
              num('min_size', '最小文件字节数', '0 = 不限制'),
            ),
            h('div', { style: { flex: '1 1 220px' } },
              num('concurrency', '同时下载数', '默认 3'),
              num('max_size_mb', '最大文件 MB', '0 = 不限制'),
            ),
          ),
        ),

        launcher && launcher.appDir
          ? h(
              Card,
              { title: '程序目录' },
              h(
                'div',
                { style: { ...S.mono, fontSize: 12, color: C.label2, wordBreak: 'break-all' } },
                launcher.appDir,
              ),
              h(
                'div',
                { style: { ...S.muted, marginTop: 5 } },
                launcher.batOk ? '已找到 启动.bat，可用概览页的开关启动。' : '这里找不到 启动.bat —— 启动开关会不可用。',
              ),
            )
          : null,

        h(
          'div',
          { style: { display: 'flex', gap: 10, alignItems: 'center' } },
          h(
            Btn,
            { variant: 'primary', disabled: saving, onClick: onSave, icon: Ico.check({ size: 13 }) },
            saving ? '保存中…' : '保存设置',
          ),
          msg ? h('span', { style: S.ok }, msg) : null,
          error ? h('span', { style: S.err }, error) : null,
        ),
        h(
          'div',
          { style: { ...S.muted } },
          '这里的设置会立刻写入归档器的 config.json；正在下载的任务不受影响。',
        ),
      )
    }

    // ---------- 整页 ----------

    function QqarkPage() {
      const [tab, setTab] = React.useState('overview')
      const [overview, setOverview] = React.useState(null)
      const [groups, setGroups] = React.useState(null)
      const [tasks, setTasks] = React.useState(null)
      const [files, setFiles] = React.useState(null)
      const [launcher, setLauncher] = React.useState(null)
      const [query, setQuery] = React.useState({ q: '', category: '', limit: 40, offset: 0, sort: 'new' })
      const [form, setForm] = React.useState(null)
      const [loading, setLoading] = React.useState(false)
      const [error, setError] = React.useState('')
      const [msg, setMsg] = React.useState('')
      const [busyId, setBusyId] = React.useState('')
      const [saving, setSaving] = React.useState(false)
      const [launching, setLaunching] = React.useState(false)
      const [sw, setSw] = React.useState(null)
      const [swLoading, setSwLoading] = React.useState(false)
      const [swBusy, setSwBusy] = React.useState(false)
      const [swResult, setSwResult] = React.useState(null)
      const [logs, setLogs] = React.useState(null)
      const [logsLoading, setLogsLoading] = React.useState(false)
      const [logView, setLogView] = React.useState('problems')
      const [baseUrl, setBaseUrl] = React.useState('http://127.0.0.1:7801')
      const [admin, setAdmin] = React.useState(() => {
        try {
          return window.localStorage.getItem('qqark.adminLaunch') !== '0'
        } catch {
          return true
        }
      })

      const flash = React.useCallback((text) => {
        setMsg(text)
        setTimeout(() => setMsg(''), 4200)
      }, [])

      const loadLauncher = React.useCallback(async () => {
        try {
          setLauncher(await apiGet('/launcher'))
        } catch {
          /* 启动器探测失败不打扰用户 */
        }
      }, [])

      const loadSwitch = React.useCallback(async () => {
        setSwLoading(true)
        try {
          setSw(await apiGet('/switch-state'))
        } catch {
          /* 切换状态探测失败不打扰用户 */
        } finally {
          setSwLoading(false)
        }
      }, [])

      const toggleSwitch = React.useCallback(
        async (on) => {
          if (swBusy) return
          setSwBusy(true)
          setSwResult({ ok: null, running: true, steps: [], error: '' })
          try {
            // 后端立刻回 {started:true}，真正的工作在它那边后台跑。
            const res = await apiPost('/switch', { on: !!on })
            if (!res || res.ok !== true) {
              const msg = (res && res.error) || '后端没有受理这次切换'
              setSwResult({ ok: false, running: false, error: msg, steps: (res && res.steps) || [] })
              flash('切换失败：' + msg)
              return
            }

            /**
             * 轮询进度，而不是干等一个 HTTP 响应。
             *
             * 一次切换要「重启 NapCat → 等它真的登上来」，正常 8~20 秒，
             * 需要现拉起 NapCat 时能到 75 秒。干等的话，任何一层的请求
             * 超时都会让用户只看到一句「切换失败」，且不知道卡在哪一步。
             * 轮询则能把每一步实时显示出来，失败时也能指出是哪一步。
             */
            let last = null
            for (let i = 0; i < 180; i++) {
              // 180 × 800ms ≈ 144 秒，比后端最坏情况再宽一点
              await new Promise((r) => setTimeout(r, 800))
              let st = null
              try {
                st = await apiGet('/switch-state')
              } catch {
                continue // 宿主偶发抽风不算失败，继续等
              }
              last = st
              setSw(st)
              const job = (st && st.job) || {}
              setSwResult({
                ok: job.ok === null ? null : job.ok === true,
                running: job.running === true,
                error: job.error || '',
                steps: Array.isArray(job.steps) ? job.steps : [],
              })
              if (!job.running) break
            }

            const job = (last && last.job) || {}
            if (job.running) {
              flash('切换还在进行中（已超过 2 分钟），进度可在卡片里看。')
            } else if (job.ok === true) {
              flash(on ? '归档模式已开启，已切到归档号。' : '归档模式已关闭，已切回日常号。')
            } else if (job.error) {
              flash('切换失败：' + job.error)
            }
          } catch (e) {
            const msg = String(e.message || e)
            setSwResult({ ok: false, running: false, error: msg, steps: [] })
            flash('切换失败：' + msg)
          } finally {
            setSwBusy(false)
            await loadSwitch()
            await loadLauncher()
          }
        },
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [swBusy, loadSwitch, loadLauncher],
      )

      const loadOverview = React.useCallback(async () => {
        setLoading(true)
        setError('')
        try {
          const data = await apiGet('/status')
          setOverview(data)
          if (data.baseUrl) setBaseUrl(data.baseUrl)
        } catch (e) {
          setOverview(null)
          setError(String(e.message || e))
        } finally {
          setLoading(false)
        }
        loadLauncher()
        loadSwitch()
      }, [loadLauncher, loadSwitch])

      const loadGroups = React.useCallback(async () => {
        setLoading(true)
        setError('')
        try {
          setGroups(await apiGet('/groups'))
        } catch (e) {
          setError(String(e.message || e))
        } finally {
          setLoading(false)
        }
      }, [])

      const loadTasks = React.useCallback(async () => {
        setLoading(true)
        setError('')
        try {
          setTasks(await apiGet('/tasks'))
        } catch (e) {
          setError(String(e.message || e))
        } finally {
          setLoading(false)
        }
      }, [])

      const loadFiles = React.useCallback(async (q) => {
        setLoading(true)
        setError('')
        try {
          const qs = new URLSearchParams()
          for (const k of ['q', 'category', 'limit', 'offset', 'sort']) {
            const v = q[k]
            if (v !== undefined && v !== null && String(v) !== '') qs.set(k, String(v))
          }
          setFiles(await apiGet('/files?' + qs.toString()))
        } catch (e) {
          setError(String(e.message || e))
        } finally {
          setLoading(false)
        }
      }, [])

      const loadConfig = React.useCallback(async () => {
        try {
          const data = await apiGet('/config')
          setForm(data.config || {})
        } catch (e) {
          setError(String(e.message || e))
        }
        loadLauncher()
      }, [loadLauncher])

      const loadLogs = React.useCallback(async (kind) => {
        setLogsLoading(true)
        setError('')
        try {
          // 不传 kind：两边都要，切视图就不用再打一次请求
          setLogs(await apiGet('/logs?limit=400'))
        } catch (e) {
          setError(String(e.message || e))
        } finally {
          setLogsLoading(false)
        }
      }, [])

      const openLogDir = async () => {
        try {
          await apiPost('/open-log-dir', {})
        } catch (e) {
          setError(String(e.message || e))
        }
      }

      React.useEffect(() => {
        if (tab === 'overview') loadOverview()
        else if (tab === 'groups') loadGroups()
        else if (tab === 'tasks') loadTasks()
        else if (tab === 'files') loadFiles(query)
        else if (tab === 'logs') loadLogs(logView)
        else if (tab === 'settings') loadConfig()
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, [tab])

      React.useEffect(() => {
        if (tab !== 'files') return undefined
        const timer = setTimeout(() => loadFiles(query), 300)
        return () => clearTimeout(timer)
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, [query, tab])

      const setAdminPref = (next) => {
        setAdmin(next)
        try {
          window.localStorage.setItem('qqark.adminLaunch', next ? '1' : '0')
        } catch {
          /* 隐私模式下写不了 localStorage，忽略 */
        }
      }

      const launch = async (elevated) => {
        setLaunching(true)
        setError('')
        try {
          const r = await apiPost('/launch', { elevated: !!elevated })
          flash(r.message || '已发起启动')
          // 给进程一点时间起来，再回读状态
          setTimeout(() => {
            loadLauncher()
            loadOverview()
          }, 2600)
        } catch (e) {
          setError(String(e.message || e))
        } finally {
          setLaunching(false)
        }
      }

      const toggleGroup = async (groupId, enabled) => {
        setBusyId(groupId)
        try {
          await apiPost('/group-enable', { group_id: groupId, enabled })
          setGroups((prev) =>
            prev ? { ...prev, groups: prev.groups.map((g) => (g.group_id === groupId ? { ...g, enabled } : g)) } : prev,
          )
        } catch (e) {
          setError(String(e.message || e))
        } finally {
          setBusyId('')
        }
      }

      const refreshGroups = async () => {
        setLoading(true)
        try {
          const r = await apiPost('/refresh-groups', {})
          flash(r.message || '已刷新')
          await loadGroups()
        } catch (e) {
          setError(String(e.message || e))
        } finally {
          setLoading(false)
        }
      }

      const triggerScan = async () => {
        try {
          const r = await apiPost('/scan', {})
          flash(r.message || '已触发补扫')
          setTimeout(loadTasks, 1500)
        } catch (e) {
          setError(String(e.message || e))
        }
      }

      const retryAll = async () => {
        try {
          const r = await apiPost('/retry', {})
          flash(r.message || '已重试')
          setTimeout(loadTasks, 1000)
        } catch (e) {
          setError(String(e.message || e))
        }
      }

      const openFolder = async (id) => {
        try {
          await apiPost('/open-folder', { id })
        } catch (e) {
          setError(String(e.message || e))
        }
      }

      const saveConfig = async () => {
        setSaving(true)
        setError('')
        try {
          const r = await apiPost('/config', { ...form })
          if (r.config) setForm(r.config)
          flash('设置已保存')
        } catch (e) {
          setError(String(e.message || e))
        } finally {
          setSaving(false)
        }
      }

      const refreshCurrent = () => {
        if (tab === 'overview') loadOverview()
        else if (tab === 'groups') loadGroups()
        else if (tab === 'tasks') loadTasks()
        else if (tab === 'files') loadFiles(query)
        else if (tab === 'logs') loadLogs(logView)
        else if (tab === 'settings') loadConfig()
      }

      const TABS = [
        ['overview', '概览'],
        ['groups', '群聊'],
        ['files', '文件'],
        ['tasks', '任务'],
        ['logs', '日志'],
        ['settings', '设置'],
      ]

      // 每个 Tab 配一个图标，扫一眼就知道去哪；order 与 TABS 的排列一致。
      const TAB_ICON = {
        overview: Ico.power,
        groups: Ico.users,
        files: Ico.archive,
        tasks: Ico.download,
        logs: Ico.list,
        settings: Ico.shield,
      }

      // 下划线式标签：选中态靠底部 2px 指示条 + 文字提亮，不再整块刷品牌色。
      // 内联样式没有 :hover，所以用 useState 手写一个悬停反馈。
      const tabBtn = (key, label) =>
        h(TabButton, {
          key,
          active: tab === key,
          label,
          icon: (TAB_ICON[key] || Ico.archive)({ size: 13 }),
          onClick: () => setTab(key),
        })

      const connected = !!(overview && overview.status && overview.status.onebot_connected)
      const running = !!(launcher && launcher.running)

      return h(
        'div',
        { style: S.page },
        h(
          'div',
          { style: S.header },
          h(
            'div',
            { style: { ...S.titleWrap, flex: 1 } },
            h(
              'div',
              { style: { display: 'flex', alignItems: 'center', gap: 8 } },
              h('span', { style: { color: C.brand, display: 'flex' } }, Ico.archive({ size: 18 })),
              h('span', { style: S.title }, 'QQ 群文件归档'),
              h(Pill, { tone: running ? 'ok' : 'bad' }, running ? '运行中' : '未运行'),
              running ? h(Pill, { tone: connected ? 'ok' : 'warn' }, connected ? 'NapCat 已连' : 'NapCat 未连') : null,
            ),
            h('div', { style: S.subtitle }, '群文件与聊天视频自动分门别类落盘'),
          ),
          h(
            'div',
            { style: { display: 'flex', gap: 8, alignItems: 'center', flex: 'none' } },
            msg ? h('span', { style: S.ok }, msg) : null,
            h(Btn, { variant: 'ghost', small: true, onClick: refreshCurrent, icon: Ico.refresh({ size: 13 }) }, '刷新'),
          ),
        ),
        h('div', { style: S.tabs }, TABS.map(([k, l]) => tabBtn(k, l))),
        h(
          'div',
          { style: S.body },
          error && tab !== 'overview'
            ? h(
                'div',
                { style: { ...S.card, borderColor: C.error, color: C.error, marginBottom: 12, fontSize: 12 } },
                error,
                h('div', { style: { ...S.muted, marginTop: 4 } }, '若提示连不上，请到「概览」用开关启动归档器。'),
              )
            : null,
          tab === 'overview'
            ? h(OverviewTab, {
                data: overview,
                loading,
                error,
                onRefresh: loadOverview,
                launcher,
                launching,
                onLaunch: launch,
                admin,
                setAdmin: setAdminPref,
                sw,
                swLoading,
                swBusy,
                swResult,
                onSwitch: toggleSwitch,
              })
            : null,
          tab === 'groups'
            ? h(GroupsTab, { data: groups, loading, error: '', onToggle: toggleGroup, busyId, onRefreshGroups: refreshGroups, msg })
            : null,
          tab === 'files'
            ? h(FilesTab, {
                data: files,
                loading,
                error: '',
                query,
                setQuery,
                onPage: (offset) => setQuery({ ...query, offset }),
                onOpenFolder: openFolder,
                baseUrl,
              })
            : null,
          tab === 'tasks'
            ? h(TasksTab, { data: tasks, loading, error: '', onScan: triggerScan, onRetry: retryAll, msg })
            : null,
          tab === 'logs'
            ? h(LogsTab, {
                data: logs,
                loading: logsLoading,
                error: '',
                view: logView,
                setView: setLogView,
                onRefresh: () => loadLogs(logView),
                onOpenDir: openLogDir,
              })
            : null,
          tab === 'settings'
            ? h(SettingsTab, { form, setForm, onSave: saveConfig, saving, msg, error: '', launcher })
            : null,
        ),
      )
    }

    // ---------- 侧边栏图标 ----------

    function QqarkIcon(props) {
      return Ico.archive({ size: (props && props.size) || 16, width: 1.35 })
    }

    module.exports = {
      inject: ['slots'],
      // 仅供离线冒烟使用：整页 QqarkPage 自己管状态，同步渲染时数据全是 null，
      // 逐个 Tab 的结构就没法验。这里把各 Tab 组件暴露出去，让冒烟能塞假数据
      // 直接渲染。Harness 只读 inject/apply，多这一个字段没有任何副作用。
      __test__: {
        OverviewTab,
        GroupsTab,
        FilesTab,
        TasksTab,
        LogsTab,
        SettingsTab,
        TabButton,
        QqarkIcon,
        S,
        C,
      },
      apply: function (ctx) {
        const slots = ctx.slots
        if (!slots || typeof slots.register !== 'function') return

        if (typeof slots.inject === 'function') {
          slots.inject('sidebar.panellist', () =>
            slots.register(
              { name: 'sidebar.panellist', id: PANEL_ID, order: PANEL_ORDER, label: () => 'QQ 归档' },
              QqarkIcon,
            ),
          )
          slots.inject('main', () => slots.register({ name: 'main', key: PANEL_ID }, QqarkPage))
        } else {
          slots.register(
            { name: 'sidebar.panellist', id: PANEL_ID, order: PANEL_ORDER, label: () => 'QQ 归档' },
            QqarkIcon,
          )
          slots.register({ name: 'main', key: PANEL_ID }, QqarkPage)
        }
      },
    }

    return module.exports
  },
})
