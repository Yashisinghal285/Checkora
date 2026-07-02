"""Game views for the Checkora chess platform."""

import json
import time
import hashlib
import secrets
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.contrib.auth.forms import AuthenticationForm
from django.core.mail import send_mail
from django.contrib import messages
from django import forms
from .forms import CustomUserCreationForm
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .engine import ChessGame
from .models import GameResult


def landing(request):
    """Render the landing page introduction to Checkora."""
    return render(request, 'game/landing.html')


@ensure_csrf_cookie
def index(request):
    """Render the board and initialise a new game in the session."""
    if 'game' not in request.session:
        game = ChessGame()
        request.session['game'] = game.to_dict()
    return render(request, 'game/board.html')


def record_game_result(mode, winner, reason):
    """Save a completed game result to the database."""
    GameResult.objects.create(mode=mode, winner=winner, end_reason=reason)


@require_POST
def make_move(request):
    """Validate and execute a chess move via the C++ engine."""
    try:
        data = json.loads(request.body)
        from_row = int(data['from_row'])
        from_col = int(data['from_col'])
        to_row = int(data['to_row'])
        to_col = int(data['to_col'])
        promotion_piece = data.get('promotion_piece', None)
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return JsonResponse(
            {'valid': False, 'message': 'Invalid request data.'},
            status=400,
        )

    game_data = request.session.get('game')
    game = ChessGame.from_dict(game_data) if game_data else ChessGame()

    success, message, captured, game_status = game.make_move(
        from_row, from_col, to_row, to_col, promotion_piece,
    )

    if success:
        request.session['game'] = game.to_dict()
        request.session.modified = True
        if game_status == 'checkmate':
            winner = 'black' if game.current_turn == 'white' else 'white'
            record_game_result(game.mode, winner, 'checkmate')
        elif game_status in ('stalemate', 'draw'):
            record_game_result(game.mode, 'draw', 'stalemate')

    return JsonResponse({
        'valid': success,
        'message': message,
        'captured': captured,
        'board': game.board,
        'current_turn': game.current_turn,
        'white_time': game.white_time,
        'black_time': game.black_time,
        'move_history': game.move_history,
        'captured_pieces': game.captured,
        'game_status': game_status,
        'draw_reason': game.draw_reason,
        'fen': game.generate_fen_key(),
        'pgn': game.generate_pgn(),
        'white_name': request.session.get('white_name', 'White'),
        'black_name': request.session.get('black_name', 'Black'),
    })


@require_GET
def valid_moves(request):
    """Return every legal destination for a piece."""
    try:
        row = int(request.GET['row'])
        col = int(request.GET['col'])
    except (KeyError, ValueError, TypeError):
        return JsonResponse({'valid_moves': []}, status=400)

    if not (0 <= row < 8 and 0 <= col < 8):
        return JsonResponse({'valid_moves': []}, status=400)

    game_data = request.session.get('game')
    if not game_data:
        return JsonResponse({'valid_moves': []})

    game = ChessGame.from_dict(game_data)
    moves = game.get_valid_moves(row, col)
    return JsonResponse({'valid_moves': moves})


@require_POST
def new_game(request):
    """Reset the game to the initial position with selected mode."""
    data = json.loads(request.body or '{}')
    mode = data.get('mode', 'pvp')
    difficulty = data.get('difficulty', 'medium')
    
    if mode not in ('pvp', 'ai'):
        mode = 'pvp'
    
    player_color = data.get('player_color', 'white')

    request.session['white_name'] = data.get('white_name', 'White')
    request.session['black_name'] = data.get('black_name', 'Black')
    player_color = data.get('player_color', 'white')
    request.session['difficulty'] = difficulty
    request.session['player_color'] = player_color

    game = ChessGame()
    game.mode = mode
    game.player_color = player_color
    game.paused = False

    request.session['game'] = game.to_dict()
    request.session.modified = True

    return JsonResponse({
        'board': game.board,
        'current_turn': game.current_turn,
        'move_history': [],
        'captured_pieces': {'white': [], 'black': []},
        'mode': game.mode,
        'player_color': game.player_color,
        # We send names back just to confirm they were saved
        'white_name': request.session['white_name'],
        'black_name': request.session['black_name'],
        'difficulty': difficulty,
        'fen': game.generate_fen_key(),
        'pgn': game.generate_pgn(),
        'game_status': game.game_status,
        'draw_reason': game.draw_reason,
    })


@require_GET
def check_promotion(request):
    """Return whether a planned move triggers pawn promotion."""
    try:
        from_row = int(request.GET['from_row'])
        from_col = int(request.GET['from_col'])
        to_row = int(request.GET['to_row'])
    except (KeyError, ValueError, TypeError):
        return JsonResponse({'is_promotion': False})

    if not (0 <= from_row < 8 and 0 <= from_col < 8 and 0 <= to_row < 8):
        return JsonResponse({'is_promotion': False})

    game_data = request.session.get('game')
    if not game_data:
        return JsonResponse({'is_promotion': False})

    is_promo = ChessGame.is_promotion_move(
        game_data['board'], from_row, from_col, to_row,
    )
    return JsonResponse({'is_promotion': is_promo})


@require_GET
def get_state(request):
    """Return the full current game state without mutating pause state."""
    game_data = request.session.get('game')
    if not game_data:
        game = ChessGame()
    else:
        game = ChessGame.from_dict(game_data)

        # Skip clock deduction if tab was closed for too long
        elapsed = time.time() - game.last_ts
        if elapsed > 10 and not game.paused:
            game.paused = True  # pause without deducting lost time
        else:
            game.update_clock()

    request.session['game'] = game.to_dict()
    request.session.modified = True

    return JsonResponse({
        'board': game.board,
        'current_turn': game.current_turn,
        'white_time': game.white_time,
        'black_time': game.black_time,
        'paused': game.paused,
        'move_history': game.move_history,
        'captured_pieces': game.captured,
        'mode': game.mode,
        'player_color': game.player_color,
        'white_name': request.session.get('white_name', 'White'),
        'black_name': request.session.get('black_name', 'Black'),
        'fen': game.generate_fen_key(),
        'pgn': game.generate_pgn(),
        'game_status': game.game_status,
        'draw_reason': game.draw_reason,
    })


@require_POST
def set_pause(request):
    """Toggle the game clock between paused and running."""
    game_data = request.session.get('game')
    if not game_data:
        return JsonResponse({'paused': False})

    data = json.loads(request.body or '{}')
    pause = data.get('pause', True)

    game = ChessGame.from_dict(game_data)

    # Only deduct elapsed time when transitioning from running to paused.
    if pause and not game.paused:
        game.update_clock()
    game.paused = pause
    game.last_ts = time.time()

    request.session['game'] = game.to_dict()
    request.session.modified = True

    return JsonResponse({
        'paused': game.paused,
        'white_time': game.white_time,
        'black_time': game.black_time,
    })


@require_POST
def ai_move(request):
    """Let the engine compute and play the best move for the current side."""
    game_data = request.session.get('game')
    if not game_data:
        err_msg = 'No active game.'
        return JsonResponse(
            {'valid': False, 'message': err_msg}, status=400
        )

    game = ChessGame.from_dict(game_data)

    if game.mode != 'ai':
        err_msg = 'Not in AI mode.'
        return JsonResponse(
            {'valid': False, 'message': err_msg}, status=400
        )

    # Depth Mapping
    difficulty = request.session.get('difficulty', 'medium')
    depth_map = {'easy': 2, 'medium': 3, 'hard': 5}
    depth = depth_map.get(difficulty, 3)

    best = game.get_ai_move(depth=depth)
    if not best:
        return JsonResponse({
            'valid': False,
            'message': 'No legal moves available.',
            'board': game.board,
            'current_turn': game.current_turn,
        })

    success, message, captured, game_status = game.make_move(
        best['from_row'], best['from_col'],
        best['to_row'],   best['to_col'],
    )

    if success:
        request.session['game'] = game.to_dict()
        request.session.modified = True

    return JsonResponse({
        'valid': success,
        'message': message,
        'captured': captured,
        'board': game.board,
        'current_turn': game.current_turn,
        'white_time': game.white_time,
        'black_time': game.black_time,
        'move_history': game.move_history,
        'captured_pieces': game.captured,
        'ai_move': best,
        'game_status': game_status,
        'draw_reason': game.draw_reason,
        'fen': game.generate_fen_key(),
        'pgn': game.generate_pgn(),
        'white_name': request.session.get('white_name', 'White'),
        'black_name': request.session.get('black_name', 'Black'),
    })


@require_POST
def offer_draw(request):
    """Handle draw offers and agreements."""
    game_data = request.session.get('game')
    if not game_data:
        err_msg = 'No active game.'
        return JsonResponse(
            {'success': False, 'message': err_msg}, status=400
        )

    data = json.loads(request.body or '{}')
    action = data.get('action')  # 'offer' or 'accept'

    if action == 'accept':
        game = ChessGame.from_dict(game_data)
        game.game_status = 'draw'
        game.draw_reason = 'agreement'
        request.session['game'] = game.to_dict()
        request.session.modified = True
        record_game_result(game.mode, 'draw', 'agreement')
        return JsonResponse({
            'success': True,
            'game_status': game.game_status,
            'draw_reason': game.draw_reason,
        })
   
    return JsonResponse({'success': True})


@require_POST
def resign_game(request):
    """Handle a player resigning the game."""
    game_data = request.session.get('game')
    if not game_data:
        err_msg = 'No active game.'
        return JsonResponse({'valid': False, 'message': err_msg}, status=400)

    game = ChessGame.from_dict(game_data)

    resigning_player = game.current_turn
    winner = 'black' if resigning_player == 'white' else 'white'

    game_status = 'resignation'

    game.game_status = game_status
    request.session['game'] = game.to_dict()
    request.session.modified = True

    record_game_result(game.mode, winner, 'resign') 

    return JsonResponse({
        'valid': True,
        'message': f'{resigning_player.capitalize()} resigned.',
        'winner': winner,
        'game_status': game_status
    })

def register_view(request):
    if request.user.is_authenticated:
        return redirect('index')
        
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False  # Deactivate account till OTP is verified
            user.save()

            # Generate 6-digit OTP
            otp = str(secrets.randbelow(900000) + 100000)
            request.session['registration_user_id'] = user.id
            # Hash OTP with SECRET_KEY as salt to prevent reading from signed cookies
            otp_hash = hashlib.sha256(f"{otp}:{settings.SECRET_KEY}".encode()).hexdigest()
            request.session['registration_otp_hash'] = otp_hash

            # Send Email
            try:
                msg_plain = (
                    f'Your OTP for registration is: {otp}\n\n'
                    'Please enter this code to activate your account.'
                )
                html_message = (
                    "<div style=\"font-family: 'Segoe UI', Arial, sans-serif; "
                    "background-color: #0f0f1a; color: #d0d0d0; padding: 40px "
                    "20px; text-align: center;\"><div style=\"background-"
                    "color: #16162a; border: 1px solid #252545; border-radius"
                    ": 12px; padding: 40px 30px; max-width: 450px; margin: 0 "
                    "auto; box-shadow: 0 10px 30px rgba(0,0,0,0.5);\">"
                    "<h1 style=\"color: #ffffff; margin-top: 0; margin-bottom"
                    ": 15px; font-size: 28px; letter-spacing: 2px;\">CHECK"
                    "<span style=\"color: #f0c040;\">ORA</span></h1>"
                    "<hr style=\"border: none; border-top: 1px solid #252545; "
                    "margin: 20px 0;\"><p style=\"color: #e0e0e0; font-size: "
                    "16px; line-height: 1.5; margin-bottom: 30px;\">Welcome "
                    "to the elite chess platform. To activate your account "
                    "and start playing, please use the verification code "
                    "below:</p><div style=\"margin: 35px 0;\"><span style=\""
                    "font-family: 'Consolas', monospace; font-size: 36px; "
                    "font-weight: bold; color: #f0c040; letter-spacing: 8px; "
                    "background: #0f0f1a; padding: 15px 25px; border-radius: "
                    "8px; border: 1px solid #3d3222; display: inline-block;"
                    "\">{otp}</span></div><p style=\"color: #8a8aaa; font-"
                    "size: 14px; margin-top: 30px;\">Enter this code on the "
                    "verification page to complete your registration.</p>"
                    "<p style=\"color: #5a5a7a; font-size: 12px; margin-top: "
                    "40px;\">If you didn't attempt to register on Checkora, "
                    "please safely ignore this email.</p></div></div>"
                ).format(otp=otp)
                send_mail(
                    'Your Checkora Verification Code',
                    msg_plain,
                    None,  # Will use EMAIL_HOST_USER
                    [user.email],
                    fail_silently=False,
                    html_message=html_message
                )
                return redirect('verify_otp')
            except Exception as e:
                # If email fails, delete the user so they can try again
                user.delete()
                err_msg = (
                    f'Failed to send OTP email: {str(e)}. '
                    'Please check your email address and try again.'
                )
                messages.error(request, err_msg)
    else:
        form = CustomUserCreationForm()
    
    return render(request, 'game/register.html', {'form': form})


def verify_otp(request):
    if request.user.is_authenticated:
        return redirect('index')
        
    user_id = request.session.get('registration_user_id')
    stored_otp_hash = request.session.get('registration_otp_hash')
    
    if not user_id or not stored_otp_hash:
        messages.error(request, 'Session expired. Please register again.')
        return redirect('register')

    if request.method == 'POST':
        entered_otp = request.POST.get('otp', '').strip()
        # Verify hash
        entered_otp_hash = hashlib.sha256(f"{entered_otp}:{settings.SECRET_KEY}".encode()).hexdigest()
        
        if entered_otp_hash == stored_otp_hash:
            try:
                user = User.objects.get(id=user_id)
                user.is_active = True
                user.save()

                # Clear session data
                del request.session['registration_user_id']
                del request.session['registration_otp_hash']

                login(request, user)
                request.session.cycle_key()  
                return redirect('index')
            
            except User.DoesNotExist:
                messages.error(
                    request, 'User not found. Please register again.'
                )
                return redirect('register')
        else:
            messages.error(request, 'Invalid OTP. Please try again.')

    return render(request, 'game/verify_otp.html')


def login_view(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            request.session.cycle_key()  
            return redirect('index')
        
    else:
        form = AuthenticationForm()

    return render(request, 'game/login.html', {'form': form})

@xframe_options_sameorigin
def rules(request):
    return render(request, 'game/rules.html')

@require_POST
def logout_view(request):
    logout(request)
    return redirect('landing')


def stats_view(request):
    """Display game statistics."""
    # from django.db.models import Count
    recent = GameResult.objects.order_by('-played_at')[:20]
    ai_results = GameResult.objects.filter(mode='ai')
    ai_wins = ai_results.filter(winner='white').count() + ai_results.filter(winner='black').count()
    ai_draws = ai_results.filter(winner='draw').count()
    ai_total = ai_results.count()
    # ai_losses = 0
    return render(request, 'game/stats.html', {
        'recent': recent,
        'ai_total': ai_total,
        'ai_wins': ai_wins,
        'ai_draws': ai_draws,
    })


def _classify_move(is_best, played_mv, best_mv, game_state):
    """Classifies a move using a basic material heuristic."""
    if is_best:
        return 'Best'
        
    # If the engine isn't returning a best move (e.g. checkmate), fallback.
    if not best_mv or not played_mv:
        return 'Mistake'
        
    # Basic heuristic: if the non-best move drops material compared to the best move,
    # it's a Blunder. Otherwise, a Mistake.
    piece_vals = {'p': 1, 'n': 3, 'b': 3, 'r': 5, 'q': 9, 'k': 0}
    
    def _capture_value(move):
        target = game_state.board[move[0]][move[1]]
        return piece_vals.get(target.lower(), 0) if target else 0

    played_val = _capture_value((played_mv['to_row'], played_mv['to_col']))
    best_val = _capture_value((best_mv['to_row'], best_mv['to_col']))
    
    if best_val > played_val + 2:
        return 'Blunder'
        
    return 'Mistake'

@require_POST
@csrf_exempt
def analyze_game_view(request):
    """POST /api/analyze-game/ — engine-powered post-game analysis."""
    try:
        body = json.loads(request.body or '{}')
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    if 'moves' not in body:
        return JsonResponse({'error': 'missing moves list.'}, status=400)
        
    moves = body.get('moves', [])
    if not isinstance(moves, list):
        return JsonResponse({'error': 'moves must be a list.'}, status=400)

    captures = checks = checkmates = promotions = blunders = mistakes = 0
    move_analysis_details = []
    game = ChessGame()
    game.white_time = 10 ** 9
    game.black_time = 10 ** 9
    
    for idx, notation in enumerate(moves[:80]): # Cap at 80 for perf
        move_num = idx // 2 + 1
        color = 'White' if idx % 2 == 0 else 'Black'

        if 'x' in notation: captures += 1
        if notation.endswith('+'): checks += 1
        if notation.endswith('#'): checkmates += 1
        if '=' in notation: promotions += 1

        best_move = None
        try:
            best_move = game.get_ai_move(depth=2)
        except Exception: pass

        # Find actual coordinates for played move by scanning board
        actual_from = actual_to = None
        clean_notation = notation.replace('+', '').replace('#', '')
        
        for r in range(8):
            for c in range(8):
                piece = game.board[r][c]
                if piece and game._color(piece) == game.current_turn:
                    for mv in game.get_valid_moves(r, c):
                        try:
                            tn = game._notation(r, c, mv['row'], mv['col'], piece, game.board[mv['row']][mv['col']], game.serialize_board(), game.serialize_castling_rights(), game._serialize_ep())
                            if tn.replace('+', '').replace('#', '') == clean_notation:
                                actual_from, actual_to = (r, c), (mv['row'], mv['col'])
                        except Exception: pass
                if actual_from: break
            if actual_from: break

        is_best = False
        played_dict = None
        if actual_from and actual_to:
            played_dict = {'to_row': actual_to[0], 'to_col': actual_to[1]}
            if best_move:
                is_best = (best_move['from_row'] == actual_from[0] and best_move['from_col'] == actual_from[1] and best_move['to_row'] == actual_to[0] and best_move['to_col'] == actual_to[1])
            else:
                is_best = True
        elif best_move is None:
            is_best = True
            
        move_class = _classify_move(is_best, played_dict, best_move, game)
        if move_class == 'Blunder': blunders += 1
        elif move_class == 'Mistake': mistakes += 1

        best_notation = '?'
        if best_move and game.board[best_move['from_row']][best_move['from_col']]:
            try:
                best_notation = game._notation(best_move['from_row'], best_move['from_col'], best_move['to_row'], best_move['to_col'], game.board[best_move['from_row']][best_move['from_col']], game.board[best_move['to_row']][best_move['to_col']], game.serialize_board(), game.serialize_castling_rights(), game._serialize_ep()).replace('+', '').replace('#', '')
            except Exception: pass

        move_analysis_details.append({'move_num': move_num, 'color': color, 'played': notation, 'best': best_notation, 'class': move_class})

        if actual_from and actual_to:
            game.make_move(actual_from[0], actual_from[1], actual_to[0], actual_to[1])
            game.last_ts = time.time()
        else: break

    total_moves = len(moves)
    bad_moves = blunders + mistakes
    accuracy = round(((total_moves - bad_moves) / total_moves) * 100) if total_moves > 0 else 100

    return JsonResponse({
        'captures': captures, 'checks': checks, 'checkmates': checkmates,
        'promotions': promotions, 'blunders': blunders, 'mistakes': mistakes, 
        'accuracy': accuracy, 'move_analysis_details': move_analysis_details,
    })