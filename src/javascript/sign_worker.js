// 持久化签名工作进程
// 从 stdin 读取输入，输出签名结果到 stdout
// 输入格式：query\tuser_agent\n
// 输出格式：signature\n

const fs = require('fs');
const path = require('path');

// 加载 x-bogus.js
const xbogusPath = path.join(__dirname, 'x-bogus.js');
const code = fs.readFileSync(xbogusPath, 'utf-8');
const signModule = { exports: {} };
const wrapper = new Function('module', 'exports', code);
wrapper(signModule, signModule.exports);
const sign = signModule.exports.sign;

// 读取 stdin
let buffer = '';
process.stdin.on('data', (data) => {
    buffer += data.toString();
    const lines = buffer.split('\n');
    buffer = lines.pop(); // 保留不完整的行
    
    for (const line of lines) {
        if (!line.trim()) continue;
        try {
            const parts = line.split('\t');
            const query = parts[0] || '';
            const userAgent = parts[1] || '';
            const result = sign(query, userAgent);
            process.stdout.write(result + '\n');
        } catch (e) {
            process.stdout.write('ERROR:' + e.message + '\n');
        }
    }
});

process.stdin.on('end', () => {
    process.exit(0);
});

// 告诉父进程已就绪
process.stdout.write('READY\n');
