/* ═══════════════════════════════════════════════════════════
   TACOS LA 26 -- App JavaScript
   Cart stored in localStorage, synced to drawer + badge
   ═══════════════════════════════════════════════════════════ */

const CART_KEY = 'tacosla26_cart';

// ── CSRF helper ───────────────────────────────────────────
function getCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function apiFetch(url, options = {}) {
  return fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken':  getCsrfToken(),
      ...(options.headers || {}),
    },
  });
}

// ── Cart helpers ──────────────────────────────────────────
function getCart() {
  try {
    return JSON.parse(localStorage.getItem(CART_KEY)) || [];
  } catch { return []; }
}

function saveCart(cart) {
  localStorage.setItem(CART_KEY, JSON.stringify(cart));
}

function addToCart(item) {
  const cart = getCart();
  cart.push(item);
  saveCart(cart);
  updateCartUI();
}

function addSimpleItem(id, name, price, type) {
  const imgMap = {
    'side': document.querySelector('[alt="Sides"]')?.src || '',
    'beverage': document.querySelector('[alt="Beverages"]')?.src || '',
  };
  const item = {
    uid: id + '_' + Date.now(),
    category: type,
    name: name,
    img: imgMap[type] || '',
    proteins: [],
    toppings: [],
    notes: '',
    unitPrice: price,
    qty: 1,
    lineTotal: price,
  };
  addToCart(item);
  showToast(name + ' added to cart!');
}

// ── Update cart badge + drawer ────────────────────────────
function updateCartUI() {
  const cart = getCart();
  const totalQty = cart.reduce((s, i) => s + i.qty, 0);

  // Badge
  const badge = document.getElementById('cartBadge');
  if (badge) badge.textContent = totalQty;

  // Drawer items
  const container = document.getElementById('cdItems');
  const footer = document.getElementById('cdFooter');
  if (!container) return;

  if (!cart.length) {
    container.innerHTML = `
      <div class="cd-empty">
        <p>Your cart is empty.</p>
        <a href="/menu" class="btn btn-sm">Browse Menu</a>
      </div>`;
    if (footer) footer.style.display = 'none';
    return;
  }

  container.innerHTML = cart.map((item, i) => {
    const proteinStr = item.proteins && item.proteins.length
      ? item.proteins.join(', ')
      : '';
    const subtitle = proteinStr
      ? `${item.qty}× &nbsp;<strong>${item.name}</strong> &mdash; ${proteinStr}`
      : `${item.qty}× &nbsp;<strong>${item.name}</strong>`;
    return `
    <div class="cd-item">
      <img src="${item.img || ''}" alt="${item.name}"/>
      <div class="cd-item-info">
        <p class="cd-item-desc">${subtitle}</p>
      </div>
      <span class="cd-item-price">$${item.lineTotal.toFixed(2)}</span>
    </div>`;
  }).join('');

  const subtotal = cart.reduce((s, i) => s + i.lineTotal, 0);
  if (footer) {
    footer.style.display = 'block';
    document.getElementById('cdSubtotal').textContent = '$' + subtotal.toFixed(2);
  }
}

// ── Cart drawer toggle ───────────────────────────────────
function toggleCartDrawer() {
  document.getElementById('cartDrawer').classList.toggle('open');
  document.getElementById('cartOverlay').classList.toggle('open');
}

// ── Toast notification ───────────────────────────────────
function showToast(msg) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}

// ── Mobile nav toggle ────────────────────────────────────
function toggleMobileNav() {
  document.getElementById('navLinks').classList.toggle('mobile-open');
}

// ── Navbar scroll shadow ─────────────────────────────────
window.addEventListener('scroll', () => {
  const nav = document.getElementById('navbar');
  if (nav) {
    nav.classList.toggle('scrolled', window.scrollY > 20);
  }
});

// ── Auto-dismiss flash messages ──────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  updateCartUI();

  // Auto-remove flash messages after 4s
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(-10px)';
      setTimeout(() => el.remove(), 300);
    }, 4000);
  });
});
