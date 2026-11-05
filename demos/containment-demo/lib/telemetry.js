// containment-demo: demo target for Theseus. Copyright (c) 2026 @Nefarious_Trey on X. MIT License.
'use strict'
// A "telemetry" dependency gone bad. When its tests run, it tries to phone home,
// write into the host user's home, read API keys from the environment, read SSH
// keys, write system dirs, flood processes and hog memory. Each attempt reports
// what happened. Theseus runs it inside the lockbox, where every attempt should fail.
const fs = require('fs')
const net = require('net')
const dns = require('dns')
const path = require('path')
const { spawn } = require('child_process')

function tryNet(host, port) {
  return new Promise((resolve) => {
    const s = net.connect({ host, port, timeout: 3000 })
    s.on('connect', () => { s.destroy(); resolve('ESCAPED: connected to ' + host + ':' + port) })
    s.on('timeout', () => { s.destroy(); resolve('blocked (timeout)') })
    s.on('error', (e) => resolve('blocked (' + e.code + ')'))
  })
}

function tryDns(name) {
  return new Promise((resolve) => {
    dns.lookup(name, (e, addr) => resolve(e ? 'blocked (' + e.code + ')' : 'ESCAPED: resolved ' + addr))
  })
}

function trySync(fn) {
  try { return 'ESCAPED: ' + fn() } catch (e) { return 'blocked (' + (e.code || e.name) + ')' }
}

function tryProcs(n) {
  return new Promise((resolve) => {
    const kids = []
    let refused = null
    for (let i = 0; i < n && !refused; i++) {
      try {
        const k = spawn('sleep', ['20'], { stdio: 'ignore' })
        k.on('error', (e) => { refused = refused || e.code })
        kids.push(k)
      } catch (e) { refused = e.code || e.name }
    }
    setTimeout(() => {
      let alive = 0
      for (const k of kids) if (k.pid && k.exitCode === null && !k.killed) { alive++; try { k.kill('SIGKILL') } catch (_) {} }
      resolve(refused ? 'blocked (' + refused + ' after ' + alive + ' processes)'
        : (alive >= n ? 'ESCAPED: started ' + alive + ' processes' : 'blocked (' + alive + ' of ' + n + ' started)'))
    }, 800)
  })
}

async function attack(hostHome, canaryName) {
  const keyNames = Object.keys(process.env).filter((k) => /KEY|TOKEN|SECRET|PASS/i.test(k))
  return {
    network_ip: await tryNet('1.1.1.1', 443),
    network_dns: await tryDns('api.vultrinference.com'),
    write_host_home: trySync(() => {
      fs.writeFileSync(path.join(hostHome, canaryName), 'owned'); return 'wrote ' + canaryName
    }),
    read_host_home: trySync(() => fs.readdirSync(hostHome).slice(0, 3).join(',') || 'listed (empty)'),
    read_ssh_keys: trySync(() => fs.readdirSync(path.join(hostHome, '.ssh')).join(',')),
    env_api_key: process.env.VULTR_INFERENCE_API_KEY ? 'ESCAPED: key value visible'
      : (keyNames.length ? 'ESCAPED: ' + keyNames.join(',') : 'blocked (no key-like variables in the env)'),
    write_system_dir: trySync(() => { fs.writeFileSync('/usr/theseus-owned', 'x'); return 'wrote /usr' }),
    process_flood: await tryProcs(Number(process.env.FLOOD_N || 600)),
  }
}

module.exports = { attack }

if (require.main === module) {
  attack(process.argv[2] || '/home', process.argv[3] || 'theseus-canary.txt')
    .then((r) => console.log(JSON.stringify(r)))
}
