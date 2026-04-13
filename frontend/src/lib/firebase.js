import { initializeApp } from "firebase/app";
import {
  getAuth,
  signInWithPopup,
  GoogleAuthProvider,
  OAuthProvider,
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
  signOut,
  onAuthStateChanged,
  updateProfile,
} from "firebase/auth";
import {
  getFirestore,
  collection,
  doc,
  setDoc,
  getDocs,
  deleteDoc,
  orderBy,
  query,
} from "firebase/firestore";

// Config

const firebaseConfig = {
  apiKey:            import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain:        import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId:         import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket:     import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId:             import.meta.env.VITE_FIREBASE_APP_ID,
};

const app  = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db   = getFirestore(app);
const googleProvider    = new GoogleAuthProvider();
const microsoftProvider = new OAuthProvider("microsoft.com");
microsoftProvider.setCustomParameters({ prompt: "select_account" });

const normalizeUser = (firebaseUser) => ({
  uid:      firebaseUser.uid,
  username: firebaseUser.displayName || firebaseUser.email?.split("@")[0] || "Usuario",
  email:    firebaseUser.email,
  photo:    firebaseUser.photoURL,
});

export const signInWithGoogle    = async () => normalizeUser((await signInWithPopup(auth, googleProvider)).user);
export const signInWithMicrosoft = async () => normalizeUser((await signInWithPopup(auth, microsoftProvider)).user);

export const registerWithEmail = async (username, email, password) => {
  const result = await createUserWithEmailAndPassword(auth, email, password);
  await updateProfile(result.user, { displayName: username });
  return normalizeUser(result.user);
};

export const signInWithEmail = async (email, password) => {
  const result = await signInWithEmailAndPassword(auth, email, password);
  return normalizeUser(result.user);
};

export const logout = () => signOut(auth);

export const onAuthChange = (callback) =>
  onAuthStateChanged(auth, (user) => callback(user ? normalizeUser(user) : null));

export { auth };

// Colecciones de Firestore

/**
 * Guarda o actualiza una conversación del usuario
 * Ruta: users/{uid}/conversations/{conversationId}
 */
export const saveConversation = async (uid, conversation) => {
  const ref = doc(db, "users", uid, "conversations", String(conversation.id));
  await setDoc(ref, {
    id:        conversation.id,
    title:     conversation.title,
    messages:  conversation.messages,
    history:   conversation.history,
    updatedAt: Date.now(),
  });
};

/**
 * Carga todas las conversaciones del usuario, ordenadas por más reciente
 */
export const loadConversations = async (uid) => {
  const ref  = collection(db, "users", uid, "conversations");
  const q    = query(ref, orderBy("updatedAt", "desc"));
  const snap = await getDocs(q);
  return snap.docs.map((d) => d.data());
};

/**
 * Elimina una conversación del usuario
 */
export const deleteConversation = async (uid, conversationId) => {
  const ref = doc(db, "users", uid, "conversations", String(conversationId));
  await deleteDoc(ref);
};